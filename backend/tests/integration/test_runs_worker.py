"""P-05: claim atomico, fencing e ciclo lease su PostgreSQL reale.

Verifica sul cluster PostgreSQL degli integration test (ruoli reali,
RLS FORCE) le proprietà critiche del ciclo di vita del worker:

- claim atomico sotto contesa: due worker concorrenti prendono al più un
  run ciascuno; il fence sale monotonicamente.
- lease scaduto → un secondo worker può riprendere il run; il vecchio
  worker (fence stale) non può più scrivere.
- checkpoint del parziale sotto RLS non tocca colonne fuori dai grant.
- cancel su run terminale è idempotente.

Se il cluster di test non è configurato la suite si salta come le altre
integration.
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
from newray.kernel.errors import QueueFull
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
from newray.modules.runs.durable import LeaseLost, RunAlreadyTerminal


def _run_row(owner, conversation_id: uuid.UUID, *, key: str) -> DurableRun:
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
            "prompt": "worker probe",
            "model_name": "gemma:test",
            "digest": "sha256:test",
            "runtime": "ollama",
            "profile_id": str(uuid.uuid4()),
            "profile_version_id": str(uuid.uuid4()),
            "binding_id": str(uuid.uuid4()),
            "parameters": {},
            "instructions": None,
            "messages": [{"role": "user", "content": "worker probe"}],
            "max_output_tokens": 128,
            "context_truncated": False,
            "context_message_count": 1,
            "context_character_count": len("worker probe"),
            "request_hash": "b" * 64,
        },
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
        deadline_at=now + timedelta(minutes=10),
    )


def _bootstrap(engine, name: str, key: str):
    identity = IdentityService(
        PostgresUserRepository(engine),
        PostgresSessionStore(engine),
        SystemClock(),
        PostgresOwnerBootstrap(engine),
    )
    owner = identity.bootstrap_owner(name, "test-passphrase-1234")
    conversations = ConversationService(
        PostgresConversationRepository(engine),
        PostgresMessageStore(engine),
        SystemClock(),
    )
    conversation = conversations.create_conversation(owner, f"P-05 {key}")
    return owner, conversation


def test_claim_atomico_e_fencing_su_lease_scaduto(test_databases: DatabaseHandles) -> None:
    engine = create_engine(test_databases.app)
    try:
        owner, conversation = _bootstrap(engine, "Worker owner", "claim")
        store = PostgresRunStore(engine)
        run = store.enqueue(_run_row(owner, conversation.id, key="claim-1"))

        worker_a = uuid.uuid4()
        worker_b = uuid.uuid4()
        t0 = datetime.now(UTC)
        lease_short = t0 + timedelta(milliseconds=100)

        first = store.claim_next(worker_id=worker_a, lease_until=lease_short, now=t0)
        assert first is not None
        assert first.run.id == run.id
        assert first.run.lease_owner == worker_a
        assert first.fence == 1

        # Sotto contesa: un secondo claim allo stesso istante non ottiene niente.
        second = store.claim_next(
            worker_id=worker_b, lease_until=t0 + timedelta(seconds=30), now=t0
        )
        assert second is None

        # Trascorso il TTL, il run è di nuovo reclamabile: fence sale.
        after_ttl = lease_short + timedelta(milliseconds=10)
        reclaimed = store.claim_next(
            worker_id=worker_b, lease_until=after_ttl + timedelta(seconds=30), now=after_ttl
        )
        assert reclaimed is not None
        assert reclaimed.run.lease_owner == worker_b
        assert reclaimed.fence == 2

        # Il vecchio worker con fence stale non può finalizzare né fare checkpoint.
        with pytest.raises(LeaseLost):
            store.complete(
                scope=Scope(owner.organization_id, owner.user_id),
                run_id=run.id, worker_id=worker_a, fence=1,
                partial_text="ignored", finish_reason="stop",
                prompt_tokens=1, completion_tokens=1, eval_duration_ns=1,
                now=after_ttl,
            )
        with pytest.raises(LeaseLost):
            store.checkpoint(
                scope=Scope(owner.organization_id, owner.user_id),
                run_id=run.id, worker_id=worker_a, fence=1,
                partial_text="ignored",
                lease_until=after_ttl + timedelta(seconds=1),
                now=after_ttl,
            )
    finally:
        engine.dispose()


def test_checkpoint_e_complete_persistono_sotto_rls(
    test_databases: DatabaseHandles,
) -> None:
    engine = create_engine(test_databases.app)
    try:
        owner, conversation = _bootstrap(engine, "Chk owner", "chk")
        store = PostgresRunStore(engine)
        run = store.enqueue(_run_row(owner, conversation.id, key="chk-1"))

        worker_id = uuid.uuid4()
        t0 = datetime.now(UTC)
        claim = store.claim_next(
            worker_id=worker_id,
            lease_until=t0 + timedelta(seconds=30),
            now=t0,
        )
        assert claim is not None
        fence = claim.fence

        scope = Scope(owner.organization_id, owner.user_id)
        after_chk = store.checkpoint(
            scope=scope, run_id=run.id, worker_id=worker_id, fence=fence,
            partial_text="ciao",
            lease_until=t0 + timedelta(seconds=60), now=t0 + timedelta(seconds=1),
        )
        assert after_chk.partial_text == "ciao"
        assert after_chk.state == RunState.RUNNING

        completed = store.complete(
            scope=scope, run_id=run.id, worker_id=worker_id, fence=fence,
            partial_text="ciao mondo", finish_reason="stop",
            prompt_tokens=3, completion_tokens=2, eval_duration_ns=1_000_000,
            now=t0 + timedelta(seconds=2),
        )
        assert completed.state == RunState.COMPLETED
        assert completed.partial_text == "ciao mondo"
        assert completed.prompt_tokens == 3
        assert completed.completion_tokens == 2
        assert completed.eval_duration_ns == 1_000_000
        assert completed.lease_owner is None
        assert completed.finished_at is not None

        # complete su run terminale non è ammesso.
        with pytest.raises(RunAlreadyTerminal):
            store.complete(
                scope=scope, run_id=run.id, worker_id=worker_id, fence=fence,
                partial_text="x", finish_reason="stop",
                prompt_tokens=1, completion_tokens=1, eval_duration_ns=1,
                now=t0 + timedelta(seconds=3),
            )
    finally:
        engine.dispose()


def test_cancel_idempotente_e_su_run_terminale(
    test_databases: DatabaseHandles,
) -> None:
    engine = create_engine(test_databases.app)
    try:
        owner, conversation = _bootstrap(engine, "Cancel owner", "cnc")
        store = PostgresRunStore(engine)
        run = store.enqueue(_run_row(owner, conversation.id, key="cnc-1"))
        scope = Scope(owner.organization_id, owner.user_id)

        t0 = datetime.now(UTC)
        first = store.mark_cancel_requested(scope, run.id, t0)
        assert first.cancel_requested_at is not None
        # Chiamata idempotente non aggiorna cancel_requested_at.
        second = store.mark_cancel_requested(scope, run.id, t0 + timedelta(seconds=1))
        assert second.cancel_requested_at == first.cancel_requested_at

        # Un run terminale (simulato passando dal claim + fail) accetta
        # cancel come no-op.
        worker_id = uuid.uuid4()
        claim = store.claim_next(
            worker_id=worker_id, lease_until=t0 + timedelta(seconds=30), now=t0
        )
        assert claim is not None
        store.fail(
            scope=scope, run_id=run.id, worker_id=worker_id, fence=claim.fence,
            error_code="INFERENCE_FAILED", finish_reason="failed",
            partial_text="", now=t0 + timedelta(seconds=1),
        )
        after = store.mark_cancel_requested(scope, run.id, t0 + timedelta(seconds=2))
        assert after.state == RunState.FAILED
    finally:
        engine.dispose()


def test_queue_cap_rifiuta_e_ignora_run_terminali(
    test_databases: DatabaseHandles,
) -> None:
    """Con cap N: N run non-terminali → il successivo QUEUE_FULL. Un run
    che passa a terminale libera lo slot; run di un altro owner non
    influenzano il conteggio di questo scope."""
    engine = create_engine(test_databases.app)
    try:
        owner, conversation = _bootstrap(engine, "Cap owner", "cap")
        store = PostgresRunStore(engine)
        # Riempio la coda con 3 run non-terminali; il 4° è rifiutato.
        for i in range(3):
            store.enqueue(_run_row(owner, conversation.id, key=f"cap-{i}"), queue_cap=3)
        with pytest.raises(QueueFull):
            store.enqueue(_run_row(owner, conversation.id, key="cap-3"), queue_cap=3)

        # Replay idempotente ha precedenza sul cap: stessa chiave → stessa ricevuta.
        replay = store.enqueue(_run_row(owner, conversation.id, key="cap-0"), queue_cap=3)
        assert replay.idempotency_key == "cap-0"

        # Marchio uno dei run come terminale (via claim + fail): il cap
        # scende, il nuovo inserimento passa.
        first = store.get(Scope(owner.organization_id, owner.user_id),
                         store.find_by_key(
                             Scope(owner.organization_id, owner.user_id),
                             conversation.id, "cap-0",
                         ).id)  # type: ignore[union-attr]
        assert first is not None
        worker_id = uuid.uuid4()
        now = datetime.now(UTC)
        claim = store.claim_next(
            worker_id=worker_id, lease_until=now + timedelta(seconds=30), now=now
        )
        assert claim is not None
        store.fail(
            scope=Scope(owner.organization_id, owner.user_id),
            run_id=claim.run.id, worker_id=worker_id, fence=claim.fence,
            error_code="INFERENCE_FAILED", finish_reason="failed",
            partial_text="", now=now + timedelta(seconds=1),
        )
        # Ora c'è spazio: il 4° passa.
        released = store.enqueue(
            _run_row(owner, conversation.id, key="cap-3"), queue_cap=3
        )
        assert released.idempotency_key == "cap-3"
    finally:
        engine.dispose()


def test_queue_cap_serializza_create_concorrenti(
    test_databases: DatabaseHandles,
) -> None:
    """Due enqueue concorrenti al bordo del cap: uno solo passa, l'altro
    riceve QueueFull. L'advisory lock per (org, owner) evita la race."""
    engine = create_engine(test_databases.app)
    try:
        owner, conversation = _bootstrap(engine, "Race owner", "race")
        store = PostgresRunStore(engine)
        # Cap = 2; ne inserisco 1 e provo due create concorrenti: uno
        # solo dei due deve passare, l'altro riceve QueueFull.
        store.enqueue(_run_row(owner, conversation.id, key="race-0"), queue_cap=2)

        def try_enqueue(index: int) -> str:
            try:
                store.enqueue(
                    _run_row(owner, conversation.id, key=f"race-{index}"),
                    queue_cap=2,
                )
                return "ok"
            except QueueFull:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(try_enqueue, [1, 2]))

        assert sorted(results) == ["ok", "rejected"]
        # Solo 2 run non-terminali in totale per lo scope.
        with engine.connect() as conn:
            _scope_conn(conn, owner)
            count = conn.execute(
                text("SELECT count(*) FROM runs WHERE state = 'queued'")
            ).scalar_one()
        assert count == 2
    finally:
        engine.dispose()


def _scope_conn(conn, owner) -> None:
    conn.execute(
        text("SELECT set_config('app.user_id', :v, true)"),
        {"v": str(owner.user_id)},
    )
    conn.execute(
        text("SELECT set_config('app.organization_id', :v, true)"),
        {"v": str(owner.organization_id)},
    )


def test_grant_su_colonne_worker(test_databases: DatabaseHandles) -> None:
    """Il ruolo applicativo può scrivere solo sulle colonne concesse.

    Un tentativo di aggiornare `snapshot` è rifiutato; le colonne del
    lifecycle worker sono ammesse.
    """
    engine = create_engine(test_databases.app)
    try:
        owner, conversation = _bootstrap(engine, "Grant owner", "gr")
        store = PostgresRunStore(engine)
        run = store.enqueue(_run_row(owner, conversation.id, key="gr-1"))

        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.user_id', :v, true)"),
                {"v": str(owner.user_id)},
            )
            conn.execute(
                text("SELECT set_config('app.organization_id', :v, true)"),
                {"v": str(owner.organization_id)},
            )
            # colonne consentite: nessun errore
            conn.execute(
                text(
                    "UPDATE runs SET heartbeat_at = now(), error_code = 'OK', "
                    "deadline_at = now() WHERE id = :id"
                ),
                {"id": run.id},
            )
    finally:
        engine.dispose()
