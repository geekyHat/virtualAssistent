"""P-05: contratto del worker con fake store, senza rete.

Copre solo la meccanica di ciclo: claim, heartbeat, finalize sotto fence
e reazione a :class:`LeaseLost`. Le prove PostgreSQL su claim atomico e
fence obsoleto stanno in ``tests/integration/test_run_worker.py``.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from newray.modules.runs import (
    ClaimedRun,
    DurableRun,
    LeaseLost,
    PartialCheckpoint,
    RunOutcome,
    RunState,
    Worker,
)


@dataclass
class _Row:
    run: DurableRun
    lease_owner: uuid.UUID | None = None
    lease_until: datetime | None = None
    fence: int = 0
    state: RunState = RunState.QUEUED


class FakeStore:
    """RunStore compatibile: le operazioni terminali sono thread-safe."""

    def __init__(self) -> None:
        self._rows: dict[uuid.UUID, _Row] = {}
        self._lock = threading.Lock()

    # helper per i test -----------------------------------------------------

    def seed_queued(self, run: DurableRun) -> None:
        self._rows[run.id] = _Row(run=run, state=RunState.QUEUED)

    def snapshot(self, run_id: uuid.UUID) -> _Row:
        return self._rows[run_id]

    def force_bump_fence(self, run_id: uuid.UUID) -> None:
        """Simula un reap: bumpa fence e restituisce a queued."""
        with self._lock:
            row = self._rows[run_id]
            row.fence += 1
            row.lease_owner = None
            row.lease_until = None
            row.state = RunState.QUEUED

    # protocollo RunStore ---------------------------------------------------

    def enqueue(self, run):  # pragma: no cover - non usato qui
        raise NotImplementedError

    def get(self, scope, run_id):  # pragma: no cover
        raise NotImplementedError

    def find_by_key(self, scope, conversation_id, idempotency_key):  # pragma: no cover
        raise NotImplementedError

    def claim(self, worker_id, lease_duration_seconds, now):
        with self._lock:
            for row in self._rows.values():
                claimable = row.state == RunState.QUEUED or (
                    row.state == RunState.RUNNING
                    and row.lease_until is not None
                    and row.lease_until < now
                )
                if not claimable:
                    continue
                row.fence += 1
                row.lease_owner = worker_id
                row.lease_until = now + timedelta(seconds=lease_duration_seconds)
                row.state = RunState.RUNNING
                return ClaimedRun(
                    run=self._project(row),
                    worker_id=worker_id,
                    lease_until=row.lease_until,
                    fence=row.fence,
                )
            return None

    def renew_lease(self, run_id, worker_id, fence, lease_until, now):
        with self._lock:
            row = self._rows.get(run_id)
            if row is None:
                return False
            if row.state != RunState.RUNNING or row.lease_owner != worker_id or row.fence != fence:
                return False
            row.lease_until = lease_until
            return True

    def save_partial(self, run_id, worker_id, fence, partial_text, now):
        with self._lock:
            row = self._rows.get(run_id)
            if row is None:
                return False
            if row.state != RunState.RUNNING or row.lease_owner != worker_id or row.fence != fence:
                return False
            row.run = _replace_partial(row.run, partial_text)
            return True

    def finalize(
        self,
        run_id,
        worker_id,
        fence,
        state,
        finish_reason,
        partial_text,
        prompt_tokens,
        completion_tokens,
        eval_duration_ns,
        now,
    ):
        with self._lock:
            row = self._rows.get(run_id)
            if row is None:
                return False
            if row.state != RunState.RUNNING or row.lease_owner != worker_id or row.fence != fence:
                return False
            row.state = state
            row.lease_owner = None
            row.lease_until = None
            row.run = _replace_terminal(
                row.run,
                partial_text,
                finish_reason,
                prompt_tokens,
                completion_tokens,
                eval_duration_ns,
            )
            return True

    def _project(self, row: _Row) -> DurableRun:
        base = row.run
        return DurableRun(
            id=base.id,
            conversation_id=base.conversation_id,
            organization_id=base.organization_id,
            owner_id=base.owner_id,
            idempotency_key=base.idempotency_key,
            payload_hash=base.payload_hash,
            state=row.state,
            snapshot=dict(base.snapshot),
            partial_text=base.partial_text,
            finish_reason=base.finish_reason,
            prompt_tokens=base.prompt_tokens,
            completion_tokens=base.completion_tokens,
            eval_duration_ns=base.eval_duration_ns,
            lease_owner=row.lease_owner,
            lease_until=row.lease_until,
            fence=row.fence,
            created_at=base.created_at,
            updated_at=base.updated_at,
        )


def _replace_partial(run: DurableRun, partial: str) -> DurableRun:
    return DurableRun(
        id=run.id,
        conversation_id=run.conversation_id,
        organization_id=run.organization_id,
        owner_id=run.owner_id,
        idempotency_key=run.idempotency_key,
        payload_hash=run.payload_hash,
        state=run.state,
        snapshot=dict(run.snapshot),
        partial_text=partial,
        finish_reason=run.finish_reason,
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        eval_duration_ns=run.eval_duration_ns,
        lease_owner=run.lease_owner,
        lease_until=run.lease_until,
        fence=run.fence,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _replace_terminal(
    run: DurableRun,
    partial: str,
    finish_reason: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    eval_duration_ns: int | None,
) -> DurableRun:
    return DurableRun(
        id=run.id,
        conversation_id=run.conversation_id,
        organization_id=run.organization_id,
        owner_id=run.owner_id,
        idempotency_key=run.idempotency_key,
        payload_hash=run.payload_hash,
        state=run.state,
        snapshot=dict(run.snapshot),
        partial_text=partial,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        eval_duration_ns=eval_duration_ns,
        lease_owner=None,
        lease_until=None,
        fence=run.fence,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


class _FixedClock:
    def __init__(self, start: datetime) -> None:
        self._t = start

    def now(self) -> datetime:
        return self._t

    def tick(self, seconds: float) -> None:
        self._t += timedelta(seconds=seconds)


def _make_run() -> DurableRun:
    now = datetime.now(UTC)
    return DurableRun(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        idempotency_key="k-1",
        payload_hash="a" * 64,
        state=RunState.QUEUED,
        snapshot={"prompt": "hi"},
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


@dataclass
class RecordingExecutor:
    outcome: RunOutcome
    partials: list[str] = field(default_factory=list)
    saw_run: DurableRun | None = None

    async def execute(self, run, checkpoint):
        self.saw_run = run
        for chunk in self.partials:
            await checkpoint.save(chunk)
        return self.outcome


def test_poll_once_su_coda_vuota_torna_false() -> None:
    store = FakeStore()
    worker = Worker(
        store,
        RecordingExecutor(outcome=RunOutcome(RunState.COMPLETED, "done", "")),
        _FixedClock(datetime.now(UTC)),
    )
    assert asyncio.run(worker.poll_once()) is False


def test_worker_finalizza_run_sotto_fence_valido() -> None:
    store = FakeStore()
    run = _make_run()
    store.seed_queued(run)
    executor = RecordingExecutor(
        outcome=RunOutcome(
            state=RunState.COMPLETED,
            finish_reason="stop",
            partial_text="ciao mondo",
            prompt_tokens=3,
            completion_tokens=4,
            eval_duration_ns=1_000_000,
        ),
        partials=["ci", "ciao ", "ciao mondo"],
    )
    worker = Worker(store, executor, _FixedClock(datetime.now(UTC)))
    took = asyncio.run(worker.poll_once())
    assert took is True
    row = store.snapshot(run.id)
    assert row.state == RunState.COMPLETED
    assert row.lease_owner is None
    assert row.run.partial_text == "ciao mondo"
    assert row.run.prompt_tokens == 3
    assert row.run.completion_tokens == 4


def test_lease_perso_durante_execute_non_finalizza() -> None:
    """Se il fence viene bumpato prima del finalize, il worker si arrende."""

    store = FakeStore()
    run = _make_run()
    store.seed_queued(run)
    executor = RecordingExecutor(
        outcome=RunOutcome(RunState.COMPLETED, "stop", "irrilevante"),
    )

    original_finalize = store.finalize
    finalize_calls: list[bool] = []

    def spy_finalize(*args, **kwargs):
        ok = original_finalize(*args, **kwargs)
        finalize_calls.append(ok)
        return ok

    store.finalize = spy_finalize  # type: ignore[assignment]

    async def scenario() -> None:
        # bumpa fence prima del claim → nessun effetto sul run già in coda
        # Poi bumpiamo durante l'esecuzione.
        real_execute = executor.execute

        async def race_execute(run_, checkpoint):
            store.force_bump_fence(run_.id)
            return await real_execute(run_, checkpoint)

        executor.execute = race_execute  # type: ignore[assignment]
        await Worker(store, executor, _FixedClock(datetime.now(UTC))).poll_once()

    asyncio.run(scenario())
    assert finalize_calls == [False]
    # Il run è di nuovo in QUEUED dopo il bump; il worker vecchio non l'ha chiuso.
    row = store.snapshot(run.id)
    assert row.state == RunState.QUEUED
    assert row.fence >= 2


def test_checkpoint_leaselost_interrompe_executor() -> None:
    """Un save_partial rifiutato solleva LeaseLost e nessun finalize segue."""

    store = FakeStore()
    run = _make_run()
    store.seed_queued(run)

    lease_lost_seen: list[Exception] = []

    class BadExecutor:
        async def execute(self, run_, checkpoint: PartialCheckpoint):
            # bump fence prima del save → checkpoint deve fallire
            store.force_bump_fence(run_.id)
            try:
                await checkpoint.save("parziale")
            except LeaseLost as exc:
                lease_lost_seen.append(exc)
                raise
            # Non deve arrivare qui.
            return RunOutcome(RunState.COMPLETED, "stop", "x")  # pragma: no cover

    worker = Worker(store, BadExecutor(), _FixedClock(datetime.now(UTC)))
    asyncio.run(worker.poll_once())
    assert len(lease_lost_seen) == 1
    row = store.snapshot(run.id)
    # Non finalizzato: il fence obsoleto ha protetto il run.
    assert row.state == RunState.QUEUED


def test_executor_error_produce_stato_failed() -> None:
    store = FakeStore()
    run = _make_run()
    store.seed_queued(run)

    class Boom:
        async def execute(self, run_, checkpoint):
            raise RuntimeError("boom")

    worker = Worker(store, Boom(), _FixedClock(datetime.now(UTC)))
    asyncio.run(worker.poll_once())
    row = store.snapshot(run.id)
    assert row.state == RunState.FAILED
    assert row.run.finish_reason is not None
    assert row.run.finish_reason.startswith("executor_error:")


def test_costruttore_rifiuta_heartbeat_maggiore_lease() -> None:
    with pytest.raises(ValueError):
        Worker(
            FakeStore(),
            RecordingExecutor(outcome=RunOutcome(RunState.COMPLETED, "done", "")),
            _FixedClock(datetime.now(UTC)),
            lease_duration_seconds=5,
            heartbeat_seconds=5,
        )
