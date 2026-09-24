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
from newray.kernel.identity import Principal as _Principal
from newray.modules.conversations import ConversationService
from newray.modules.models import (
    RUNTIME_OLLAMA,
    ChatRequest,
    Completion,
    ContentDelta,
    StreamEvent,
    ToolCallRequest,
    ToolSchema,
)
from newray.modules.runs import (
    FINISH_REASON_CANCELLED_BY_USER,
    FINISH_REASON_DEADLINE_EXCEEDED,
    FINISH_REASON_EMPTY_OUTPUT,
    FINISH_REASON_MODEL_ERROR,
    FINISH_REASON_TOOL_BUDGET_EXCEEDED,
    DurableRun,
    RunState,
    RunWorker,
    ToolInvocationState,
    ToolResult,
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
        cancel_requested_at=None,
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


_RUN_STATUS_SCHEMA = ToolSchema(
    name="run.status",
    description="stato del run",
    parameters={
        "type": "object",
        "properties": {"run_id": {"type": "string"}},
        "required": ["run_id"],
    },
)


class _FakeGateway:
    """Gateway tool di test: offre ``run.status`` e ritorna un esito fisso."""

    def __init__(self, result: ToolResult | None = None) -> None:
        self._result = result or ToolResult(content='{"state": "running"}')
        self.calls: list[ToolCallRequest] = []

    def tool_schemas(self, principal: _Principal) -> tuple[ToolSchema, ...]:
        return (_RUN_STATUS_SCHEMA,)

    def invoke(self, principal: _Principal, call: ToolCallRequest) -> ToolResult:
        self.calls.append(call)
        return self._result


class _ToolThenAnswerChatModel:
    """Primo turno: una richiesta tool. Turni successivi: risposta finale."""

    def __init__(self) -> None:
        self.turns = 0
        self.seen_tool_message = False

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.turns += 1
        turn = self.turns
        # Il turno di continuazione deve vedere il messaggio TOOL nel contesto.
        if any(m.role.value == "tool" for m in request.messages):
            self.seen_tool_message = True

        async def generate() -> AsyncIterator[StreamEvent]:
            if turn == 1:
                yield ToolCallRequest(
                    call_id="c1",
                    name="run.status",
                    arguments={"run_id": "00000000-0000-0000-0000-000000000001"},
                )
                yield Completion(finish_reason="tool_calls", prompt_tokens=3, completion_tokens=1)
            else:
                yield ContentDelta(text="Il run è in corso.")
                yield Completion(
                    finish_reason="stop",
                    prompt_tokens=6,
                    completion_tokens=4,
                    eval_duration_ns=1_000_000,
                )

        return generate()


class _AlwaysToolChatModel:
    """Chiede un tool a ogni turno: prova il limite di turni tool."""

    def __init__(self) -> None:
        self.turns = 0

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.turns += 1
        n = self.turns

        async def generate() -> AsyncIterator[StreamEvent]:
            yield ToolCallRequest(
                call_id=f"c{n}",
                name="run.status",
                arguments={"run_id": "00000000-0000-0000-0000-000000000001"},
            )
            yield Completion(finish_reason="tool_calls", prompt_tokens=1, completion_tokens=1)

        return generate()


def test_ciclo_tool_esegue_registra_e_continua_sullo_stesso_modello() -> None:
    async def scenario() -> None:
        principal = Principal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), Role.OWNER)
        conversations, conversation_id = _conversations_with_conversation(principal)
        store = InMemoryRunStore()
        run = _queued_run(store, conversation_id, principal.organization_id, principal.user_id)
        gateway = _FakeGateway(ToolResult(content='{"found": true, "state": "running"}'))
        chat_model = _ToolThenAnswerChatModel()
        worker = RunWorker(
            store,
            conversations,
            chat_model,
            tool_gateway=gateway,
            lease_seconds=5,
            heartbeat_interval_seconds=10,
        )

        await worker._claim_and_execute()

        finalized = store.get(principal.scope, run.id)
        assert finalized is not None
        assert finalized.state is RunState.COMPLETED
        assert finalized.partial_text == "Il run è in corso."
        # Il modello ha chiesto il tool una volta e ha continuato sullo stesso run.
        assert [c.name for c in gateway.calls] == ["run.status"]
        assert chat_model.seen_tool_message is True

        invocations = store.list_tool_invocations(principal.scope, run.id)
        assert len(invocations) == 1
        assert invocations[0].tool_name == "run.status"
        assert invocations[0].state is ToolInvocationState.SUCCEEDED
        assert invocations[0].result == '{"found": true, "state": "running"}'

        types = [e.type for e in store.list_events(principal.scope, run.id, 0).events]
        assert "tool.executing" in types
        assert "tool.succeeded" in types

        # Il messaggio persistito porta la risposta finale, non il risultato tool.
        messages = conversations.list_messages(principal, conversation_id)
        assert [m.content for m in messages] == ["Ciao", "Il run è in corso."]

    asyncio.run(scenario())


def test_tool_result_errore_registra_failed_ma_il_run_continua() -> None:
    async def scenario() -> None:
        principal = Principal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), Role.OWNER)
        conversations, conversation_id = _conversations_with_conversation(principal)
        store = InMemoryRunStore()
        run = _queued_run(store, conversation_id, principal.organization_id, principal.user_id)
        gateway = _FakeGateway(
            ToolResult(
                content='{"error": "tool_not_allowed"}',
                is_error=True,
                error_code="tool_not_allowed",
            )
        )
        worker = RunWorker(
            store,
            conversations,
            _ToolThenAnswerChatModel(),
            tool_gateway=gateway,
            lease_seconds=5,
            heartbeat_interval_seconds=10,
        )

        await worker._claim_and_execute()

        invocations = store.list_tool_invocations(principal.scope, run.id)
        assert len(invocations) == 1
        assert invocations[0].state is ToolInvocationState.FAILED
        assert invocations[0].error_code == "tool_not_allowed"
        types = [e.type for e in store.list_events(principal.scope, run.id, 0).events]
        assert "tool.failed" in types
        # L'errore del tool è un dato per il modello, non un guasto del run:
        # la generazione continua e completa.
        finalized = store.get(principal.scope, run.id)
        assert finalized is not None
        assert finalized.state is RunState.COMPLETED

    asyncio.run(scenario())


def test_limite_turni_tool_ferma_con_tool_budget_exceeded() -> None:
    async def scenario() -> None:
        principal = Principal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), Role.OWNER)
        conversations, conversation_id = _conversations_with_conversation(principal)
        store = InMemoryRunStore()
        run = _queued_run(store, conversation_id, principal.organization_id, principal.user_id)
        gateway = _FakeGateway()
        worker = RunWorker(
            store,
            conversations,
            _AlwaysToolChatModel(),
            tool_gateway=gateway,
            lease_seconds=5,
            heartbeat_interval_seconds=10,
            max_tool_calls=2,
        )

        await worker._claim_and_execute()

        finalized = store.get(principal.scope, run.id)
        assert finalized is not None
        assert finalized.state is RunState.FAILED
        assert finalized.finish_reason == FINISH_REASON_TOOL_BUDGET_EXCEEDED
        # Esattamente due turni tool eseguiti prima del limite.
        assert len(store.list_tool_invocations(principal.scope, run.id)) == 2
        assert conversations.list_messages(principal, conversation_id) == []

    asyncio.run(scenario())


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


def test_cancel_durante_generazione_finalizza_cancelled_senza_messaggio() -> None:
    """§8.3: stop propagato dal worker, parziale preservato, mai un messaggio."""

    async def scenario() -> None:
        principal = Principal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), Role.OWNER)
        conversations, conversation_id = _conversations_with_conversation(principal)
        store = InMemoryRunStore()
        run = _queued_run(store, conversation_id, principal.organization_id, principal.user_id)
        chat_model = _ScriptedChatModel(
            [
                ContentDelta(text="prima della richiesta di stop"),
                0.1,
                ContentDelta(text="mai visto"),
                Completion(finish_reason="stop", prompt_tokens=1, completion_tokens=1),
            ]
        )
        worker = RunWorker(
            store, conversations, chat_model, lease_seconds=1, heartbeat_interval_seconds=0.02
        )

        task = asyncio.create_task(worker._claim_and_execute())
        await asyncio.sleep(0.03)
        cancelled = store.request_cancel(principal.scope, run.id)
        assert cancelled is not None and cancelled.state is RunState.RUNNING
        await task

        finalized = store.get(principal.scope, run.id)
        assert finalized is not None
        assert finalized.state is RunState.CANCELLED
        assert finalized.finish_reason == FINISH_REASON_CANCELLED_BY_USER
        assert finalized.partial_text == "prima della richiesta di stop"
        assert conversations.list_messages(principal, conversation_id) == []

    asyncio.run(scenario())


def test_cancel_su_run_queued_transita_subito_senza_worker() -> None:
    async def scenario() -> None:
        principal = Principal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), Role.OWNER)
        conversations, conversation_id = _conversations_with_conversation(principal)
        store = InMemoryRunStore()
        run = _queued_run(store, conversation_id, principal.organization_id, principal.user_id)

        cancelled = store.request_cancel(principal.scope, run.id)

        assert cancelled is not None
        assert cancelled.state is RunState.CANCELLED
        assert cancelled.finish_reason == FINISH_REASON_CANCELLED_BY_USER
        page = store.list_events(principal.scope, run.id, 0)
        assert [event.type for event in page.events] == ["run.queued", "run.cancelled"]

    asyncio.run(scenario())
