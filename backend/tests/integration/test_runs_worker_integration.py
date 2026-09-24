"""P-05: claim/lease/fencing/reclaim del worker su PostgreSQL reale.

Le funzioni SQL SECURITY DEFINER (ADR 0007, migrazione 0010) sono provate
qui con il ruolo applicativo reale (``newray_app``), non con i fake: claim
concorrente, esclusività della risorsa, worker verificato morto e fencing
sul finalize. Nessuna inferenza reale: solo il meccanismo del worker.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

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


def _backdate_lease(engine, owner, run_id: uuid.UUID) -> None:
    """Simula un worker morto: lease scaduta, mai registrato in run_workers."""
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.user_id', :v, true)"), {"v": str(owner.user_id)})
        conn.execute(
            text("SELECT set_config('app.organization_id', :v, true)"),
            {"v": str(owner.organization_id)},
        )
        conn.execute(
            text("UPDATE runs SET lease_until = now() - interval '1 hour' WHERE id = :id"),
            {"id": run_id},
        )


def test_claim_concorrente_vince_una_sola_grazie_al_resource_lease(
    test_databases: DatabaseHandles,
) -> None:
    engine = create_engine(test_databases.app)
    try:
        identity = IdentityService(
            PostgresUserRepository(engine),
            PostgresSessionStore(engine),
            SystemClock(),
            PostgresOwnerBootstrap(engine),
        )
        owner = identity.bootstrap_owner("Worker owner", "test-passphrase-1234")
        conversations = ConversationService(
            PostgresConversationRepository(engine), PostgresMessageStore(engine), SystemClock()
        )
        conversation = conversations.create_conversation(owner, "P-05 claim")
        store = PostgresRunStore(engine)
        store.enqueue(_queued_run(owner, conversation.id, key="run-a"))
        store.enqueue(_queued_run(owner, conversation.id, key="run-b"))

        worker_a, worker_b = uuid.uuid4(), uuid.uuid4()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda worker: store.claim(worker, 30, RESOURCE), (worker_a, worker_b))
            )

        claimed = [r for r in results if r is not None]
        assert len(claimed) == 1, "una sola risorsa: un solo claim vince"
        assert claimed[0].state is RunState.RUNNING
        assert claimed[0].fence == 1
    finally:
        engine.dispose()


def test_heartbeat_checkpoint_e_finalize_fencing_checked(
    test_databases: DatabaseHandles,
) -> None:
    engine = create_engine(test_databases.app)
    try:
        identity = IdentityService(
            PostgresUserRepository(engine),
            PostgresSessionStore(engine),
            SystemClock(),
            PostgresOwnerBootstrap(engine),
        )
        owner = identity.bootstrap_owner("Heartbeat owner", "test-passphrase-1234")
        conversations = ConversationService(
            PostgresConversationRepository(engine), PostgresMessageStore(engine), SystemClock()
        )
        conversation = conversations.create_conversation(owner, "P-05 heartbeat")
        store = PostgresRunStore(engine)
        store.enqueue(_queued_run(owner, conversation.id, key="run-hb"))

        worker = uuid.uuid4()
        claimed = store.claim(worker, 30, RESOURCE)
        assert claimed is not None

        checkpointed = store.heartbeat(claimed.id, worker, claimed.fence, 30, "parziale…", RESOURCE)
        assert checkpointed is not None
        assert checkpointed.partial_text == "parziale…"

        # Fence sbagliato: un altro worker non può fare checkpoint al posto suo.
        impostor = uuid.uuid4()
        assert store.heartbeat(claimed.id, impostor, claimed.fence, 30, "rubato", RESOURCE) is None

        finalized = store.finalize(
            claimed.id,
            worker,
            claimed.fence,
            RunState.COMPLETED,
            "stop",
            5,
            2,
            1_000_000,
            "parziale…finale",
            RESOURCE,
        )
        assert finalized is not None
        assert finalized.state is RunState.COMPLETED
        assert finalized.partial_text == "parziale…finale"

        # Il run è terminale: un secondo finalize (anche con fence corretto)
        # non trova più state='running' e fallisce.
        assert (
            store.finalize(
                claimed.id,
                worker,
                claimed.fence,
                RunState.FAILED,
                "model_error",
                None,
                None,
                None,
                "altro",
                RESOURCE,
            )
            is None
        )

        # La risorsa è stata rilasciata dal finalize: un secondo run in coda
        # è ora reclamabile dallo stesso o da un altro worker.
        store.enqueue(_queued_run(owner, conversation.id, key="run-hb-2"))
        second = store.claim(uuid.uuid4(), 30, RESOURCE)
        assert second is not None
        assert second.idempotency_key == "run-hb-2"
    finally:
        engine.dispose()


def test_reclaim_worker_morto_e_vecchio_worker_non_puo_finalizzare(
    test_databases: DatabaseHandles,
) -> None:
    engine = create_engine(test_databases.app)
    try:
        identity = IdentityService(
            PostgresUserRepository(engine),
            PostgresSessionStore(engine),
            SystemClock(),
            PostgresOwnerBootstrap(engine),
        )
        owner = identity.bootstrap_owner("Reclaim owner", "test-passphrase-1234")
        conversations = ConversationService(
            PostgresConversationRepository(engine), PostgresMessageStore(engine), SystemClock()
        )
        conversation = conversations.create_conversation(owner, "P-05 reclaim")
        store = PostgresRunStore(engine)
        run = store.enqueue(_queued_run(owner, conversation.id, key="run-dead"))

        dead_worker = uuid.uuid4()
        claimed = store.claim(dead_worker, 30, RESOURCE)
        assert claimed is not None
        checkpointed = store.heartbeat(
            claimed.id, dead_worker, claimed.fence, 30, "quasi fatto", RESOURCE
        )
        assert checkpointed is not None

        # Il worker non è mai stato registrato in run_workers (mai avviato
        # con .register(), o crashato prima): reclaim_stale lo considera
        # verificato morto non appena il lease scade, senza aspettare oltre.
        _backdate_lease(engine, owner, run.id)
        recovered = store.reclaim_stale(grace_seconds=0)
        recovered_ids = {r.id for r in recovered}
        assert claimed.id in recovered_ids
        interrupted = next(r for r in recovered if r.id == claimed.id)
        assert interrupted.state is RunState.INTERRUPTED
        assert interrupted.finish_reason == "worker_lost"
        assert interrupted.partial_text == "quasi fatto"

        # Il vecchio worker, tornando "in vita", non può più finalizzare:
        # lo stato non è più 'running' (fencing/stato, non solo il fence).
        assert (
            store.finalize(
                claimed.id,
                dead_worker,
                claimed.fence,
                RunState.COMPLETED,
                "stop",
                1,
                1,
                1,
                "troppo tardi",
                RESOURCE,
            )
            is None
        )
        # Neanche un checkpoint tardivo viene accettato.
        assert (
            store.heartbeat(claimed.id, dead_worker, claimed.fence, 30, "troppo tardi", RESOURCE)
            is None
        )

        # La risorsa è stata rilasciata dal reclaim: un nuovo run è reclamabile.
        store.enqueue(_queued_run(owner, conversation.id, key="run-dead-2"))
        assert store.claim(uuid.uuid4(), 30, RESOURCE) is not None
    finally:
        engine.dispose()


def test_count_active_scoped_per_organizzazione(test_databases: DatabaseHandles) -> None:
    engine = create_engine(test_databases.app)
    try:
        identity = IdentityService(
            PostgresUserRepository(engine),
            PostgresSessionStore(engine),
            SystemClock(),
            PostgresOwnerBootstrap(engine),
        )
        owner = identity.bootstrap_owner("Queue owner", "test-passphrase-1234")
        conversations = ConversationService(
            PostgresConversationRepository(engine), PostgresMessageStore(engine), SystemClock()
        )
        conversation = conversations.create_conversation(owner, "P-05 queue")
        store = PostgresRunStore(engine)

        assert store.count_active(owner.scope) == 0
        store.enqueue(_queued_run(owner, conversation.id, key="q-1"))
        store.enqueue(_queued_run(owner, conversation.id, key="q-2"))
        assert store.count_active(owner.scope) == 2

        run = store.claim(uuid.uuid4(), 30, RESOURCE)
        assert run is not None
        # queued + running contano entrambi come "attivi".
        assert store.count_active(owner.scope) == 2

        store.finalize(
            run.id, run.lease_owner, run.fence, RunState.COMPLETED, "stop", 1, 1, 1, "ok", RESOURCE
        )
        assert store.count_active(owner.scope) == 1

        # Uno scope diverso (altra org) non vede la coda di owner.
        other_scope = Scope(uuid.uuid4(), uuid.uuid4())
        assert store.count_active(other_scope) == 0
    finally:
        engine.dispose()
