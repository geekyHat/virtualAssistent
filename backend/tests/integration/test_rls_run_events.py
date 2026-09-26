"""P-06: RLS sulla outbox eventi e ordering della sequence.

- Un principal estraneo non vede gli eventi di un altro utente.
- Le sequence sono monotone crescenti e uniche per run.
- ``list_events_after`` restituisce solo eventi con ``sequence > after``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from conftest import DatabaseHandles
from sqlalchemy import text

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
from newray.modules.runs import DurableRun, RunEventType, RunState
from newray.modules.runs.adapters.postgres import PostgresRunStore


def _seed_run(store: PostgresRunStore, owner, conversation_id: uuid.UUID) -> DurableRun:
    now = datetime.now(UTC)
    return store.enqueue(
        DurableRun(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            organization_id=owner.organization_id,
            owner_id=owner.user_id,
            idempotency_key="events-key",
            payload_hash="d" * 64,
            state=RunState.QUEUED,
            snapshot={"prompt": "rls events"},
            partial_text="",
            finish_reason=None,
            prompt_tokens=None,
            completion_tokens=None,
            eval_duration_ns=None,
            lease_owner=None,
            lease_until=None,
            fence=0,
            cancel_requested_at=None,
            created_at=now,
            updated_at=now,
        )
    )


def test_eventi_sono_ordinati_e_isolati_per_scope(test_databases: DatabaseHandles) -> None:
    engine = create_engine(test_databases.app)
    try:
        identity = IdentityService(
            PostgresUserRepository(engine),
            PostgresSessionStore(engine),
            SystemClock(),
            PostgresOwnerBootstrap(engine),
        )
        alice = identity.bootstrap_owner("Alice", "test-passphrase-alice-x")
        conversations = ConversationService(
            PostgresConversationRepository(engine),
            PostgresMessageStore(engine),
            SystemClock(),
        )
        conv = conversations.create_conversation(alice, "rls events")
        store = PostgresRunStore(engine)
        run = _seed_run(store, alice, conv.id)
        worker_id = uuid.uuid4()
        t0 = datetime.now(UTC)
        claimed = store.claim(worker_id, lease_duration_seconds=30, now=t0)
        assert claimed is not None

        # Tre delta + un terminale → 4 eventi con sequence 1..4.
        for i, chunk in enumerate(["a", "b", "c"], start=1):
            ok = store.save_partial(
                run_id=run.id,
                worker_id=worker_id,
                fence=claimed.fence,
                partial_text="abc"[:i],
                delta_text=chunk,
                now=t0 + timedelta(seconds=i),
            )
            assert ok is True
        ok = store.finalize(
            run_id=run.id,
            worker_id=worker_id,
            fence=claimed.fence,
            state=RunState.COMPLETED,
            finish_reason="stop",
            partial_text="abc",
            prompt_tokens=1,
            completion_tokens=3,
            eval_duration_ns=1_000_000,
            now=t0 + timedelta(seconds=4),
        )
        assert ok is True

        # Owner vede la sequence completa.
        events = store.list_events_after(alice.scope, run.id, 0, 100)
        assert [e.sequence for e in events] == [1, 2, 3, 4]
        assert [e.event_type for e in events] == [
            RunEventType.DELTA,
            RunEventType.DELTA,
            RunEventType.DELTA,
            RunEventType.COMPLETED,
        ]

        # Cursore: dopo 2 → solo 3, 4.
        tail = store.list_events_after(alice.scope, run.id, 2, 100)
        assert [e.sequence for e in tail] == [3, 4]

        # Uno scope estraneo (senza worker_id) non vede alcun evento.
        stranger = Scope(uuid.uuid4(), uuid.uuid4())
        assert store.list_events_after(stranger, run.id, 0, 100) == []

        # RLS anche a livello SQL: manipolazione diretta con scope
        # estraneo, nessuna riga visibile.
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.user_id', :v, true)"),
                {"v": str(stranger.user_id)},
            )
            conn.execute(
                text("SELECT set_config('app.organization_id', :v, true)"),
                {"v": str(stranger.organization_id)},
            )
            row = conn.execute(
                text("SELECT count(*) AS n FROM run_events WHERE run_id = :id"),
                {"id": run.id},
            ).one()
            assert row.n == 0
    finally:
        engine.dispose()
