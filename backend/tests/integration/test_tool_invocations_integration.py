"""P-07: ricevute tool ed eventi del ciclo tool su PostgreSQL reale.

Prova la funzione ``newray_record_tool_event`` (SECURITY DEFINER, migrazione
0012) con il ruolo applicativo reale: ricevuta idempotente per
``(run_id, call_id)``, fencing (solo il worker con il fence corrente scrive),
eventi ``tool.*`` in ordine nel log, e isolamento RLS della ricevuta.
"""

from __future__ import annotations

import uuid
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
from newray.modules.runs import DurableRun, RunState, ToolInvocationState
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
        snapshot={"prompt": "prova", "model_name": "gemma:test", "digest": "sha256:test"},
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


def _fixture(engine):  # type: ignore[no-untyped-def]
    identity = IdentityService(
        PostgresUserRepository(engine),
        PostgresSessionStore(engine),
        SystemClock(),
        PostgresOwnerBootstrap(engine),
    )
    owner = identity.bootstrap_owner("Tool owner", "test-passphrase-1234")
    conversations = ConversationService(
        PostgresConversationRepository(engine), PostgresMessageStore(engine), SystemClock()
    )
    conversation = conversations.create_conversation(owner, "P-07 tool")
    return owner, conversation


def test_ricevuta_idempotente_ed_eventi_in_ordine(test_databases: DatabaseHandles) -> None:
    engine = create_engine(test_databases.app)
    try:
        owner, conversation = _fixture(engine)
        store = PostgresRunStore(engine)
        run = store.enqueue(_queued_run(owner, conversation.id, key="run-a"))
        worker = uuid.uuid4()
        claimed = store.claim(worker, 30, RESOURCE)
        assert claimed is not None and claimed.id == run.id

        invocation_id = uuid.uuid4()
        # executing → succeeded sullo stesso call_id/invocation_id.
        updated = store.record_tool_event(
            run.id,
            worker,
            claimed.fence,
            invocation_id,
            "call-1",
            "run.status",
            {"run_id": str(run.id)},
            ToolInvocationState.EXECUTING,
            "tool.executing",
            None,
            None,
        )
        assert updated is not None
        updated = store.record_tool_event(
            run.id,
            worker,
            claimed.fence,
            invocation_id,
            "call-1",
            "run.status",
            {"run_id": str(run.id)},
            ToolInvocationState.SUCCEEDED,
            "tool.succeeded",
            '{"found": true}',
            None,
        )
        assert updated is not None

        invocations = store.list_tool_invocations(owner.scope, run.id)
        assert len(invocations) == 1  # idempotente per (run_id, call_id)
        assert invocations[0].state is ToolInvocationState.SUCCEEDED
        assert invocations[0].result == '{"found": true}'
        assert invocations[0].error_code is None

        # Eventi in ordine: run.started (dal claim) poi i due tool.*.
        events = store.list_events(owner.scope, run.id, 0).events
        tool_events = [e.type for e in events if e.type.startswith("tool.")]
        assert tool_events == ["tool.executing", "tool.succeeded"]
        # Sequenze monotone strettamente crescenti.
        sequences = [e.sequence for e in events]
        assert sequences == sorted(sequences)
        assert len(set(sequences)) == len(sequences)
    finally:
        engine.dispose()


def test_fencing_vecchio_worker_non_registra(test_databases: DatabaseHandles) -> None:
    engine = create_engine(test_databases.app)
    try:
        owner, conversation = _fixture(engine)
        store = PostgresRunStore(engine)
        run = store.enqueue(_queued_run(owner, conversation.id, key="run-b"))
        worker = uuid.uuid4()
        claimed = store.claim(worker, 30, RESOURCE)
        assert claimed is not None

        # Fence sbagliato (un worker più recente): nessuna scrittura.
        result = store.record_tool_event(
            run.id,
            worker,
            claimed.fence + 1,
            uuid.uuid4(),
            "call-x",
            "run.status",
            {"run_id": str(run.id)},
            ToolInvocationState.EXECUTING,
            "tool.executing",
            None,
            None,
        )
        assert result is None
        assert store.list_tool_invocations(owner.scope, run.id) == ()
        tool_events = [
            e.type
            for e in store.list_events(owner.scope, run.id, 0).events
            if e.type.startswith("tool.")
        ]
        assert tool_events == []
    finally:
        engine.dispose()


def test_ricevuta_isolata_per_scope(test_databases: DatabaseHandles) -> None:
    engine = create_engine(test_databases.app)
    try:
        owner, conversation = _fixture(engine)
        store = PostgresRunStore(engine)
        run = store.enqueue(_queued_run(owner, conversation.id, key="run-c"))
        worker = uuid.uuid4()
        claimed = store.claim(worker, 30, RESOURCE)
        assert claimed is not None
        store.record_tool_event(
            run.id,
            worker,
            claimed.fence,
            uuid.uuid4(),
            "call-1",
            "run.status",
            {"run_id": str(run.id)},
            ToolInvocationState.SUCCEEDED,
            "tool.succeeded",
            '{"ok": true}',
            None,
        )

        assert len(store.list_tool_invocations(owner.scope, run.id)) == 1
        # Altro proprietario nella stessa organizzazione: RLS non fa emergere
        # la ricevuta (nessun errore distinto, solo vuoto).
        other = Scope(owner.organization_id, uuid.uuid4())
        assert store.list_tool_invocations(other, run.id) == ()
    finally:
        engine.dispose()
