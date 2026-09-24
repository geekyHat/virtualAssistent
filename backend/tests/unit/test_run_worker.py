"""P-05: worker dei run durevoli — claim, checkpoint, deadline, fencing.

Fake deterministiche (``InMemoryRunStore``, modelli locali): nessuna
inferenza reale. Il fencing/claim/lease su PostgreSQL reale è negli
integration test (NewRay.md §22.2).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fakes import (
    FakeClock,
    InMemoryConversationRepository,
    InMemoryMessageStore,
    InMemoryRunStore,
)
from newray.kernel.identity import Principal, Role
from newray.modules.conversations import ConversationService
from newray.modules.models import (
    RUNTIME_OLLAMA,
    ChatRequest,
    Completion,
    ContentDelta,
    StreamEvent,
)
from newray.modules.runs import (
    FINISH_REASON_DEADLINE_EXCEEDED,
    FINISH_REASON_EMPTY_OUTPUT,
    FINISH_REASON_MODEL_ERROR,
    DurableRun,
    RunState,
    RunWorker,
)


def _snapshot(prompt: str) -> dict[str, object]:
    return {
        "model_name": "gemma:test",
        "runtime": RUNTIME_OLLAMA,
        "parameters": {"options": {"temperature": 0}},
        "messages": [
            {"role": "system", "content": "Istruzioni"},
            {"role": "user", "content": prompt},
        ],
        "prompt": prompt,
        "max_output_tokens": 2048,
    }


def _queued_run(
    store: InMemoryRunStore,
    conversation_id: uuid.UUID,
    organization_id: uuid.UUID,
    owner_id: uuid.UUID,
    *,
    idempotency_key: str = "key-1",
    prompt: str = "Ciao",
) -> DurableRun:
    now = datetime.now(UTC)
    run = DurableRun(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        organization_id=organization_id,
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        payload_hash="a" * 64,
        state=RunState.QUEUED,
        snapshot=_snapshot(prompt),
        partial_text="",
        finish_reason=None,
        prompt_tokens=None,
        completion_tokens=None,
        eval_duration_ns=None,
        lease_owner=None,
        lease_until=None,
        fence=0,
        created_at=now,
        updated_at=now,
    )
    return store.enqueue(run)


def _conversations_with_conversation(
    principal: Principal,
) -> tuple[ConversationService, uuid.UUID]:
    conversations = ConversationService(
        InMemoryConversationRepository(),
        InMemoryMessageStore(),
        FakeClock(datetime(2026, 9, 24, tzinfo=UTC)),
    )
    conversation = conversations.create_conversation(principal, "Test")
    return conversations, conversation.id


class _ScriptedChatModel:
    """Modello che riproduce una sequenza fissa di eventi/eccezioni."""

    def __init__(self, steps: list[StreamEvent | Exception | float]) -> None:
        self._steps = steps

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        async def generate() -> AsyncIterator[StreamEvent]:
            for step in self._steps:
                if isinstance(step, float):
                    await asyncio.sleep(step)
                elif isinstance(step, Exception):
                    raise step
                else:
                    yield step

        return generate()


class _FencingLossStore(InMemoryRunStore):
    """Wrapper: il primo heartbeat fallisce sempre (fencing perso)."""

    def __init__(self) -> None:
        super().__init__()
        self.heartbeat_calls = 0

    def heartbeat(self, *args: object, **kwargs: object) -> DurableRun | None:
        self.heartbeat_calls += 1
        return None


def test_happy_path_claim_checkpoint_finalize_e_persistenza_messaggio() -> None:
    async def scenario() -> None:
        principal = Principal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), Role.OWNER)
        conversations, conversation_id = _conversations_with_conversation(principal)
        store = InMemoryRunStore()
        run = _queued_run(store, conversation_id, principal.organization_id, principal.user_id)
        chat_model = _ScriptedChatModel(
            [
                ContentDelta(text="Ciao "),
                ContentDelta(text="mondo"),
                Completion(
                    finish_reason="stop",
                    prompt_tokens=5,
                    completion_tokens=2,
                    eval_duration_ns=1_000_000,
                ),
            ]
        )
        worker = RunWorker(
            store, conversations, chat_model, lease_seconds=5, heartbeat_interval_seconds=10
        )

        claimed = await worker._claim_and_execute()
        assert claimed is True

        finalized = store.get(principal.scope, run.id)
        assert finalized is not None
        assert finalized.state is RunState.COMPLETED
        assert finalized.finish_reason == "stop"
        assert finalized.partial_text == "Ciao mondo"
        assert finalized.prompt_tokens == 5
        assert finalized.completion_tokens == 2

        messages = conversations.list_messages(principal, conversation_id)
        assert [m.content for m in messages] == ["Ciao", "Ciao mondo"]

    asyncio.run(scenario())


def test_deadline_superata_fallisce_e_preserva_parziale() -> None:
    async def scenario() -> None:
        principal = Principal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), Role.OWNER)
        conversations, conversation_id = _conversations_with_conversation(principal)
        store = InMemoryRunStore()
        run = _queued_run(store, conversation_id, principal.organization_id, principal.user_id)
        chat_model = _ScriptedChatModel(
            [
                ContentDelta(text="parziale"),
                0.05,
                ContentDelta(text="mai visto"),
                Completion(finish_reason="stop", prompt_tokens=1, completion_tokens=1),
            ]
        )
        worker = RunWorker(
            store,
            conversations,
            chat_model,
            lease_seconds=5,
            heartbeat_interval_seconds=10,
            max_duration_seconds=0,
        )

        await worker._claim_and_execute()

        finalized = store.get(principal.scope, run.id)
        assert finalized is not None
        assert finalized.state is RunState.FAILED
        assert finalized.finish_reason == FINISH_REASON_DEADLINE_EXCEEDED
        assert finalized.partial_text == "parziale"
        assert conversations.list_messages(principal, conversation_id) == []

    asyncio.run(scenario())


def test_stream_vuoto_fallisce_con_empty_output() -> None:
    async def scenario() -> None:
        principal = Principal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), Role.OWNER)
        conversations, conversation_id = _conversations_with_conversation(principal)
        store = InMemoryRunStore()
        run = _queued_run(store, conversation_id, principal.organization_id, principal.user_id)
        worker = RunWorker(
            store,
            conversations,
            _ScriptedChatModel([]),
            lease_seconds=5,
            heartbeat_interval_seconds=10,
        )

        await worker._claim_and_execute()

        finalized = store.get(principal.scope, run.id)
        assert finalized is not None
        assert finalized.state is RunState.FAILED
        assert finalized.finish_reason == FINISH_REASON_EMPTY_OUTPUT

    asyncio.run(scenario())


def test_errore_del_modello_fallisce_preservando_il_parziale() -> None:
    async def scenario() -> None:
        principal = Principal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), Role.OWNER)
        conversations, conversation_id = _conversations_with_conversation(principal)
        store = InMemoryRunStore()
        run = _queued_run(store, conversation_id, principal.organization_id, principal.user_id)
        chat_model = _ScriptedChatModel(
            [ContentDelta(text="prima del guasto"), RuntimeError("runtime rifiutato")]
        )
        worker = RunWorker(
            store, conversations, chat_model, lease_seconds=5, heartbeat_interval_seconds=10
        )

        await worker._claim_and_execute()

        finalized = store.get(principal.scope, run.id)
        assert finalized is not None
        assert finalized.state is RunState.FAILED
        assert finalized.finish_reason == FINISH_REASON_MODEL_ERROR
        assert finalized.partial_text == "prima del guasto"

    asyncio.run(scenario())


def test_fencing_perso_non_scrive_stato_ne_messaggi() -> None:
    """Un worker più recente possiede già il run: nessuna scrittura finale."""

    async def scenario() -> None:
        principal = Principal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), Role.OWNER)
        conversations, conversation_id = _conversations_with_conversation(principal)
        store = _FencingLossStore()
        run = _queued_run(store, conversation_id, principal.organization_id, principal.user_id)
        chat_model = _ScriptedChatModel(
            [
                ContentDelta(text="in corso"),
                0.05,
                ContentDelta(text="altro"),
                Completion(finish_reason="stop", prompt_tokens=1, completion_tokens=1),
            ]
        )
        worker = RunWorker(
            store, conversations, chat_model, lease_seconds=1, heartbeat_interval_seconds=0.01
        )

        await worker._claim_and_execute()

        assert store.heartbeat_calls >= 1
        current = store.get(principal.scope, run.id)
        assert current is not None
        # Ancora "running": nessun finalize è mai passato il controllo di
        # fencing (il worker non scrive nulla dopo aver perso la lease).
        assert current.state is RunState.RUNNING
        assert conversations.list_messages(principal, conversation_id) == []

    asyncio.run(scenario())
