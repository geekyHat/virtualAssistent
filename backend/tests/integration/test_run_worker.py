"""P-05: claim atomico, fencing e finalize sotto lease su PostgreSQL reale.

Copre le proprietà che il fake in ``tests/unit/test_worker_loop.py`` non
può provare: la corsa fra due worker deve produrre un solo vincitore, un
lease scaduto deve rendere il run riclaimabile e il worker precedente non
deve poter finalizzare. Segue le regole del backend AGENTS.md: ruolo
applicativo (senza BYPASSRLS), niente fake per il contratto DB.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
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
from newray.modules.runs import DurableRun, RunState
from newray.modules.runs.adapters.postgres import PostgresRunStore


def _make_run(owner, conversation_id: uuid.UUID, key: str) -> DurableRun:
    now = datetime.now(UTC)
    return DurableRun(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        organization_id=owner.organization_id,
        owner_id=owner.user_id,
        idempotency_key=key,
        payload_hash="c" * 64,
        state=RunState.QUEUED,
        snapshot={"prompt": "worker prova"},
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


@pytest.fixture()
def enqueued_run(test_databases: DatabaseHandles):
    engine = create_engine(test_databases.app)
    identity = IdentityService(
        PostgresUserRepository(engine),
        PostgresSessionStore(engine),
        SystemClock(),
        PostgresOwnerBootstrap(engine),
    )
    owner = identity.bootstrap_owner("Worker owner", "test-passphrase-1234")
    conversations = ConversationService(
        PostgresConversationRepository(engine),
        PostgresMessageStore(engine),
        SystemClock(),
    )
    conversation = conversations.create_conversation(owner, "P-05 worker")
    store = PostgresRunStore(engine)
    run = store.enqueue(_make_run(owner, conversation.id, key="worker-1"))
    try:
        yield engine, store, owner, run
    finally:
        engine.dispose()


def test_due_worker_stessa_coda_un_solo_vincitore(enqueued_run) -> None:
    _engine, store, _owner, run = enqueued_run
    worker_a = uuid.uuid4()
    worker_b = uuid.uuid4()
    now = datetime.now(UTC)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [
            pool.submit(store.claim, worker_a, 30, now),
            pool.submit(store.claim, worker_b, 30, now),
        ]
        results = [f.result() for f in futs]

    winners = [c for c in results if c is not None]
    assert len(winners) == 1, "solo un worker deve acquisire il run"
    claimed = winners[0]
    assert claimed.run.id == run.id
    assert claimed.fence == 1
    assert claimed.run.state == RunState.RUNNING
    # Un secondo claim immediato (coda vuota) non deve ritornare nulla.
    assert store.claim(worker_a, 30, now) is None


def test_lease_scaduto_riclaimabile_e_vecchio_worker_non_finalizza(enqueued_run) -> None:
    _engine, store, _owner, run = enqueued_run

    worker_old = uuid.uuid4()
    t0 = datetime.now(UTC)
    first = store.claim(worker_old, lease_duration_seconds=30, now=t0)
    assert first is not None
    assert first.fence == 1

    # Il lease vive fino a t0 + 30s: riclaim a t0 + 60s deve riuscire.
    worker_new = uuid.uuid4()
    t1 = t0 + timedelta(seconds=60)
    second = store.claim(worker_new, lease_duration_seconds=30, now=t1)
    assert second is not None
    assert second.run.id == run.id
    assert second.fence == 2
    assert second.run.lease_owner == worker_new

    # Il vecchio worker non deve poter finalizzare, né rinnovare, né checkpoint.
    ok = store.finalize(
        run_id=run.id,
        worker_id=worker_old,
        fence=1,
        state=RunState.COMPLETED,
        finish_reason="stop",
        partial_text="postumo",
        prompt_tokens=1,
        completion_tokens=1,
        eval_duration_ns=1,
        now=t1,
    )
    assert ok is False, "un worker con fence obsoleto non può finalizzare"

    ok_renew = store.renew_lease(
        run_id=run.id,
        worker_id=worker_old,
        fence=1,
        lease_until=t1 + timedelta(seconds=30),
        now=t1,
    )
    assert ok_renew is False

    ok_partial = store.save_partial(
        run_id=run.id,
        worker_id=worker_old,
        fence=1,
        partial_text="postumo",
        now=t1,
    )
    assert ok_partial is False

    # Il nuovo worker invece può finalizzare regolarmente.
    ok_new = store.finalize(
        run_id=run.id,
        worker_id=worker_new,
        fence=second.fence,
        state=RunState.COMPLETED,
        finish_reason="stop",
        partial_text="ciao mondo",
        prompt_tokens=2,
        completion_tokens=3,
        eval_duration_ns=1_000_000,
        now=t1,
    )
    assert ok_new is True


def test_renew_lease_su_fence_valido_prolunga(enqueued_run) -> None:
    _engine, store, _owner, run = enqueued_run
    worker_id = uuid.uuid4()
    t0 = datetime.now(UTC)
    claimed = store.claim(worker_id, lease_duration_seconds=30, now=t0)
    assert claimed is not None
    new_lease = t0 + timedelta(seconds=90)
    ok = store.renew_lease(
        run_id=run.id,
        worker_id=worker_id,
        fence=claimed.fence,
        lease_until=new_lease,
        now=t0 + timedelta(seconds=10),
    )
    assert ok is True
    # Il run non è più riclaimabile fino allo scadere del nuovo lease.
    other = uuid.uuid4()
    assert store.claim(other, lease_duration_seconds=30, now=t0 + timedelta(seconds=60)) is None


def test_partial_sopravvive_al_riclaim_dopo_lease_scaduto(enqueued_run) -> None:
    """Kill/restart: il parziale scritto dal vecchio worker resta visibile."""

    _engine, store, _owner, run = enqueued_run
    worker_a = uuid.uuid4()
    t0 = datetime.now(UTC)
    first = store.claim(worker_a, lease_duration_seconds=30, now=t0)
    assert first is not None
    ok = store.save_partial(
        run_id=run.id,
        worker_id=worker_a,
        fence=first.fence,
        partial_text="parziale-A",
        now=t0 + timedelta(seconds=1),
    )
    assert ok is True

    # Simulazione crash: nessun finalize, il lease scade.
    worker_b = uuid.uuid4()
    second = store.claim(
        worker_b,
        lease_duration_seconds=30,
        now=t0 + timedelta(seconds=60),
    )
    assert second is not None
    assert second.fence == first.fence + 1
    # Il claim non azzera partial_text: il nuovo worker vede il testo del vecchio.
    assert second.run.partial_text == "parziale-A"
    # Il nuovo worker può proseguire e finalizzare.
    ok_partial = store.save_partial(
        run_id=run.id,
        worker_id=worker_b,
        fence=second.fence,
        partial_text="parziale-A + coda-B",
        now=t0 + timedelta(seconds=61),
    )
    assert ok_partial is True
    ok_final = store.finalize(
        run_id=run.id,
        worker_id=worker_b,
        fence=second.fence,
        state=RunState.COMPLETED,
        finish_reason="stop",
        partial_text="parziale-A + coda-B",
        prompt_tokens=None,
        completion_tokens=None,
        eval_duration_ns=None,
        now=t0 + timedelta(seconds=62),
    )
    assert ok_final is True
    persisted = store.get(_owner.scope, run.id)
    assert persisted is not None
    assert persisted.state == RunState.COMPLETED
    assert persisted.partial_text == "parziale-A + coda-B"


def test_worker_id_vuoto_non_bypassa_isolamento(test_databases: DatabaseHandles) -> None:
    """Senza ``app.worker_id`` la policy worker non deve concedere accesso."""

    engine = create_engine(test_databases.app)
    try:
        identity = IdentityService(
            PostgresUserRepository(engine),
            PostgresSessionStore(engine),
            SystemClock(),
            PostgresOwnerBootstrap(engine),
        )
        alice = identity.bootstrap_owner("Alice", "test-passphrase-alice")
        conversations = ConversationService(
            PostgresConversationRepository(engine),
            PostgresMessageStore(engine),
            SystemClock(),
        )
        conv = conversations.create_conversation(alice, "P-05 rls")
        store = PostgresRunStore(engine)
        run_a = store.enqueue(_make_run(alice, conv.id, key="alice-1"))

        # Un principal diverso, ``app.worker_id`` NON impostato, non deve
        # vedere il run di Alice attraverso i canali ordinari.
        stranger = Scope(organization_id=uuid.uuid4(), user_id=uuid.uuid4())
        assert store.get(stranger, run_a.id) is None

        # Manipolazione diretta: imposta lo scope estraneo senza worker_id e
        # tenta di leggere/aggiornare il run di Alice; RLS deve nasconderlo.
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.user_id', :v, true)"),
                {"v": str(stranger.user_id)},
            )
            conn.execute(
                text("SELECT set_config('app.organization_id', :v, true)"),
                {"v": str(stranger.organization_id)},
            )
            visible = conn.execute(
                text("SELECT id FROM runs WHERE id = :id"), {"id": run_a.id}
            ).first()
            assert visible is None
            # Anche l'UPDATE non tocca il run: rowcount 0.
            result = conn.execute(
                text("UPDATE runs SET partial_text = 'x' WHERE id = :id"),
                {"id": run_a.id},
            )
            assert result.rowcount == 0
    finally:
        engine.dispose()
