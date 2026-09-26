"""P-05: ownership cooperativa del worker durante il lifespan API."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from test_worker_loop import FakeStore, RecordingExecutor, _FixedClock  # noqa: E402

from newray.modules.runs import (
    DurableRun,
    RunLauncher,
    RunOutcome,
    RunState,
    Worker,
)


def _make_run() -> DurableRun:
    now = datetime.now(UTC)
    return DurableRun(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        idempotency_key="k",
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
        cancel_requested_at=None,
        created_at=now,
        updated_at=now,
    )


def test_launcher_start_stop_svuota_la_coda() -> None:
    async def scenario() -> None:
        store = FakeStore()
        run = _make_run()
        store.seed_queued(run)
        executor = RecordingExecutor(
            outcome=RunOutcome(RunState.COMPLETED, "stop", "ok"),
        )
        worker = Worker(store, executor, _FixedClock(datetime.now(UTC)))
        launcher = RunLauncher(worker, idle_backoff_seconds=0.01)
        await launcher.start()
        # Attesa breve per lasciare al worker il tempo di consumare.
        deadline = asyncio.get_event_loop().time() + 1.0
        while asyncio.get_event_loop().time() < deadline:
            if store.snapshot(run.id).state == RunState.COMPLETED:
                break
            await asyncio.sleep(0.02)
        await launcher.stop(timeout=1.0)
        assert store.snapshot(run.id).state == RunState.COMPLETED

    asyncio.run(scenario())


def test_launcher_stop_su_worker_idle_non_solleva() -> None:
    async def scenario() -> None:
        store = FakeStore()
        executor = RecordingExecutor(outcome=RunOutcome(RunState.COMPLETED, "stop", ""))
        worker = Worker(store, executor, _FixedClock(datetime.now(UTC)))
        launcher = RunLauncher(worker, idle_backoff_seconds=0.01)
        await launcher.start()
        await asyncio.sleep(0.05)
        await launcher.stop(timeout=1.0)

    asyncio.run(scenario())


def test_launcher_doppio_start_solleva() -> None:
    async def scenario() -> None:
        store = FakeStore()
        executor = RecordingExecutor(outcome=RunOutcome(RunState.COMPLETED, "stop", ""))
        worker = Worker(store, executor, _FixedClock(datetime.now(UTC)))
        launcher = RunLauncher(worker, idle_backoff_seconds=0.01)
        await launcher.start()
        try:
            with pytest.raises(RuntimeError):
                await launcher.start()
        finally:
            await launcher.stop(timeout=1.0)

    asyncio.run(scenario())


def test_launcher_stop_prima_di_start_no_op() -> None:
    async def scenario() -> None:
        store = FakeStore()
        executor = RecordingExecutor(outcome=RunOutcome(RunState.COMPLETED, "stop", ""))
        worker = Worker(store, executor, _FixedClock(datetime.now(UTC)))
        launcher = RunLauncher(worker, idle_backoff_seconds=0.01)
        # stop senza start: no-op, non solleva.
        await launcher.stop(timeout=0.1)

    asyncio.run(scenario())


def test_launcher_rifiuta_backoff_non_positivo() -> None:
    store = FakeStore()
    executor = RecordingExecutor(outcome=RunOutcome(RunState.COMPLETED, "stop", ""))
    worker = Worker(store, executor, _FixedClock(datetime.now(UTC)))
    with pytest.raises(ValueError):
        RunLauncher(worker, idle_backoff_seconds=0)
