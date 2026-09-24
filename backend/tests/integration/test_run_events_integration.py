"""P-06: eventi durevoli, retention/resync e cancellazione su PostgreSQL reale.

Le funzioni SQL SECURITY DEFINER (ADR 0007, migrazione 0011) sono provate
con il ruolo applicativo reale: sequenza monotona, RLS su ``run_events``,
retention dei soli ``message.delta`` con ``resync`` su cursore scaduto,
cancellazione di un run ``queued`` (diretta) e di un run ``running``
(propagata da un worker reale con modello fake lento).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from conftest import DatabaseHandles

from newray.infrastructure.database import create_engine
from newray.kernel.clock import SystemClock
from newray.kernel.identity import Scope
from newray.modules.conversations import ConversationService
from newray.modules.conversations.adapters.postgres import (
    PostgresConversationRepository,
    PostgresMessageStore,
)
from newray.modules.identity import IdentityService
from newray.modules.identity.adapters.postgres import (
    PostgresOwnerBootstrap,
    PostgresSessionStore,
    PostgresUserRepository,
)
from newray.modules.models import RUNTIME_OLLAMA, ChatRequest, Completion, ContentDelta, StreamEvent
from newray.modules.runs import DurableRun, RunState, RunWorker
from newray.modules.runs.adapters.postgres import PostgresRunStore

RESOURCE = "gpu:0"


def _queued_run(owner, conversation_id: uuid.UUID, *, key: str) -> DurableRun:
    now = datetime.now(UTC)
    return DurableRun(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        organization_id=owner.organization_id,
        owner_id=owner.user_id,
        idempotency_key=key,
        payload_hash="a" * 64,
        state=RunState.QUEUED,
        snapshot={
            "prompt": "prova",
            "model_name": "gemma:test",
            "runtime": RUNTIME_OLLAMA,
            "parameters": {},
            "messages": [{"role": "user", "content": "prova"}],
            "max_output_tokens": 2048,
        },
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


def _setup(engine):
    identity = IdentityService(
        PostgresUserRepository(engine),
        PostgresSessionStore(engine),
        SystemClock(),
        PostgresOwnerBootstrap(engine),
    )
    owner = identity.bootstrap_owner("Events owner", "test-passphrase-1234")
    conversations = ConversationService(
        PostgresConversationRepository(engine), PostgresMessageStore(engine), SystemClock()
    )
    conversation = conversations.create_conversation(owner, "P-06")
    return owner, conversations, conversation


def test_eventi_in_ordine_e_isolati_per_scope(test_databases: DatabaseHandles) -> None:
    engine = create_engine(test_databases.app)
    try:
        owner, conversations, conversation = _setup(engine)
        store = PostgresRunStore(engine)
        run = store.enqueue(_queued_run(owner, conversation.id, key="run-events"))

        worker_id = uuid.uuid4()
        claimed = store.claim(worker_id, 30, RESOURCE)
        assert claimed is not None
        store.heartbeat(claimed.id, worker_id, claimed.fence, 30, "ok", RESOURCE)
        store.finalize(
            claimed.id,
            worker_id,
            claimed.fence,
            RunState.COMPLETED,
            "stop",
            1,
            1,
            1,
            "ok",
            RESOURCE,
        )

        page = store.list_events(owner.scope, run.id, 0)
        assert [e.type for e in page.events] == [
            "run.queued",
            "run.started",
            "message.delta",
            "run.completed",
        ]
        assert [e.sequence for e in page.events] == [1, 2, 3, 4]
        assert page.events[-1].payload["text"] == "ok"
        assert page.gap is False
        assert page.latest_sequence == 4

        # Un'altra organizzazione non vede nulla (RLS FORCE su run_events).
        other_scope = Scope(uuid.uuid4(), uuid.uuid4())
        other_page = store.list_events(other_scope, run.id, 0)
        assert other_page.events == ()
    finally:
        engine.dispose()


def test_retention_pota_i_delta_e_resync_su_cursore_vecchio(
    test_databases: DatabaseHandles,
) -> None:
    engine = create_engine(test_databases.app)
    try:
        owner, conversations, conversation = _setup(engine)
        store = PostgresRunStore(engine)
        run = store.enqueue(_queued_run(owner, conversation.id, key="run-retention"))
        worker_id = uuid.uuid4()
        claimed = store.claim(worker_id, 30, RESOURCE)
        assert claimed is not None

        # Più di MAX_RETAINED_DELTA_EVENTS (50) checkpoint: solo gli ultimi
        # 50 message.delta restano, run.queued/run.started mai potati.
        for i in range(60):
            updated = store.heartbeat(
                claimed.id, worker_id, claimed.fence, 30, f"testo-{i}", RESOURCE
            )
            assert updated is not None

        page_from_start = store.list_events(owner.scope, run.id, 0)
        delta_events = [e for e in page_from_start.events if e.type == "message.delta"]
        assert len(delta_events) == 50
        assert delta_events[0].payload["text"] == "testo-10"  # i primi 10 potati
        assert delta_events[-1].payload["text"] == "testo-59"

        # Il cursore "dopo run.started" (sequence=2) è più vecchio del
        # primo delta rimasto: buco → resync, non un replay silenzioso.
        page_gap = store.list_events(owner.scope, run.id, 2)
        assert page_gap.gap is True
        assert page_gap.latest_sequence == page_from_start.latest_sequence
    finally:
        engine.dispose()


def test_cancel_su_run_queued_transita_subito(test_databases: DatabaseHandles) -> None:
    engine = create_engine(test_databases.app)
    try:
        owner, conversations, conversation = _setup(engine)
        store = PostgresRunStore(engine)
        run = store.enqueue(_queued_run(owner, conversation.id, key="run-cancel-queued"))

        cancelled = store.request_cancel(owner.scope, run.id)

        assert cancelled is not None
        assert cancelled.state is RunState.CANCELLED
        assert cancelled.finish_reason == "cancelled_by_user"
        page = store.list_events(owner.scope, run.id, 0)
        assert [e.type for e in page.events] == ["run.queued", "run.cancelled"]

        # Idempotente: una seconda richiesta non cambia nulla né duplica l'evento.
        again = store.request_cancel(owner.scope, run.id)
        assert again is not None and again.state is RunState.CANCELLED
        assert len(store.list_events(owner.scope, run.id, 0).events) == 2
    finally:
        engine.dispose()


class _SlowChatModel:
    """Modello fake che si ferma a metà finché non viene liberato."""

    def __init__(self) -> None:
        self.resume = asyncio.Event()

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        async def generate() -> AsyncIterator[StreamEvent]:
            yield ContentDelta(text="prima dello stop")
            await self.resume.wait()
            yield ContentDelta(text="mai visto")
            yield Completion(finish_reason="stop", prompt_tokens=1, completion_tokens=1)

        return generate()


def test_cancel_su_run_running_propagato_dal_worker_reale(
    test_databases: DatabaseHandles,
) -> None:
    async def scenario() -> None:
        engine = create_engine(test_databases.app)
        try:
            owner, conversations, conversation = _setup(engine)
            store = PostgresRunStore(engine)
            run = store.enqueue(_queued_run(owner, conversation.id, key="run-cancel-running"))
            chat_model = _SlowChatModel()
            worker = RunWorker(
                store, conversations, chat_model, lease_seconds=5, heartbeat_interval_seconds=0.1
            )

            task = asyncio.create_task(worker._claim_and_execute())
            await asyncio.sleep(0.3)
            cancelled = await asyncio.to_thread(store.request_cancel, owner.scope, run.id)
            assert cancelled is not None and cancelled.state is RunState.RUNNING
            # Lascia al prossimo heartbeat (ogni 0.1s) il tempo di osservare
            # cancel_requested_at PRIMA di liberare il modello: altrimenti
            # lo stream potrebbe attraversare tutto il resto senza mai
            # ripassare dal controllo di cancellazione.
            await asyncio.sleep(0.3)
            chat_model.resume.set()
            await task

            finalized = store.get(owner.scope, run.id)
            assert finalized is not None
            assert finalized.state is RunState.CANCELLED
            assert finalized.finish_reason == "cancelled_by_user"
            assert finalized.partial_text == "prima dello stop"
            assert conversations.list_messages(owner, conversation.id) == []

            page = store.list_events(owner.scope, run.id, 0)
            assert page.events[-1].type == "run.cancelled"
            assert page.events[-1].payload["text"] == "prima dello stop"
        finally:
            engine.dispose()

    asyncio.run(scenario())
