"""Contesa GPU: `FsComputeLease` serializza due worker sullo stesso host.

Il test simula due processi worker sulla stessa macchina che condividono
il file di lock. In un ambiente reale sono processi UNIX distinti che
aprono lo stesso path; `fcntl.flock` è avvisorio per-file per-host e
concede il lock a un solo file descriptor alla volta.

Copre anche il recupero automatico: quando il file descriptor del
detentore viene chiuso (equivalente alla morte del processo), il kernel
rilascia il lock e un secondo worker può prenderlo — senza intervento
dell'applicazione.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from newray.kernel.identity import new_id
from newray.modules.models import Completion, ContentDelta
from newray.modules.runs import DurableRunWorker, RunState, WorkerConfig
from newray.modules.runs.adapters.compute_lease import FsComputeLease
from tests.unit.test_durable_worker import (
    FakeRunStore,
    RecordingConversations,
    ScriptedChatModel,
    TickingClock,
    _queued_run,
    _replace,
    _run_async,
)


def test_fs_compute_lease_esclusivo(tmp_path: Path) -> None:
    """Due lease sullo stesso file: solo uno acquisisce; il release lo libera."""
    lock_path = tmp_path / "compute.lock"
    a = FsComputeLease(lock_path)
    b = FsComputeLease(lock_path)

    assert a.try_acquire() is True
    assert a.held is True
    assert b.try_acquire() is False  # A detiene → B rifiutato

    a.release()
    assert a.held is False
    assert b.try_acquire() is True   # A rilasciato → B ora può
    b.release()


def test_fs_compute_lease_recupero_su_morte_processo(tmp_path: Path) -> None:
    """Se il fd viene chiuso (simulazione morte del processo), il kernel
    rilascia il flock e un altro processo può prenderlo. Niente lavoro
    manuale di pulizia."""
    lock_path = tmp_path / "compute.lock"
    a = FsComputeLease(lock_path)
    b = FsComputeLease(lock_path)

    assert a.try_acquire() is True
    # Simuliamo la morte del processo A: chiudiamo il fd senza chiamare
    # release(). Il kernel deve rilasciare il flock lo stesso.
    fd = a._fd  # accesso deliberato per simulare crash
    assert fd is not None
    os.close(fd)
    a._fd = None  # evita double-close nel garbage collection

    # B deve poter acquisire subito.
    assert b.try_acquire() is True
    b.release()


def test_secondo_worker_non_processa_finche_primo_detiene_lease(
    tmp_path: Path,
) -> None:
    """Due worker sullo stesso store con lo stesso file di lock: mentre A
    detiene il lease, B non ottiene claim (il compute lease è controllato
    prima di `claim_next`, quindi il run resta in coda).
    """
    async def scenario() -> None:
        store = FakeRunStore()
        # Due run indipendenti in coda.
        run_a = _queued_run(prompt="uno")
        run_b = _queued_run(prompt="due")
        # Distinguiamo la chiave e l'id per non collidere.
        run_b = _replace(
            run_b,
            idempotency_key="key-2",
            created_at=run_a.created_at + timedelta(seconds=1),
        )
        store.put(run_a)
        store.put(run_b)

        lock_path = tmp_path / "compute.lock"

        chat_a = ScriptedChatModel([
            ContentDelta("out-a"),
            Completion(finish_reason="stop", prompt_tokens=1, completion_tokens=1),
        ])
        chat_b = ScriptedChatModel([
            ContentDelta("out-b"),
            Completion(finish_reason="stop", prompt_tokens=1, completion_tokens=1),
        ])
        conv_a = RecordingConversations()
        conv_b = RecordingConversations()

        clock = TickingClock(datetime(2026, 9, 27, 12, 0, tzinfo=UTC))
        cfg = WorkerConfig(
            poll_interval=0.01, lease_ttl=timedelta(seconds=30),
            heartbeat_interval=timedelta(seconds=10),
            checkpoint_min_interval=timedelta(0),
            cancel_check_interval=timedelta(milliseconds=50),
            inactivity_timeout=timedelta(seconds=60),
        )
        worker_a = DurableRunWorker(
            store=store,  # type: ignore[arg-type]
            chat_model=chat_a,  # type: ignore[arg-type]
            conversations=conv_a,  # type: ignore[arg-type]
            config=cfg, clock=clock, worker_id=new_id(),
            compute_lease=FsComputeLease(lock_path),
        )
        worker_b = DurableRunWorker(
            store=store,  # type: ignore[arg-type]
            chat_model=chat_b,  # type: ignore[arg-type]
            conversations=conv_b,  # type: ignore[arg-type]
            config=cfg, clock=clock, worker_id=new_id(),
            compute_lease=FsComputeLease(lock_path),
        )

        # A tiene il lock manualmente prima del suo tick, come farebbe se
        # fosse in mezzo a un'inference in corso su un processo separato.
        held = worker_a._compute_lease
        assert held.try_acquire() is True
        try:
            # B tenta un tick: non riesce ad acquisire il lease → return False,
            # nessun claim, nessuna inference.
            assert await worker_b._tick() is False
            assert store.snapshot(run_a.id).state == RunState.QUEUED
            assert store.snapshot(run_b.id).state == RunState.QUEUED
            assert chat_b.requests == []
        finally:
            held.release()

        # Ora, senza contesa, A processa e completa il proprio claim.
        assert await worker_a._tick() is True
        # Uno dei due run è ora completato; l'altro resta in coda per il
        # prossimo tick, con B che finalmente può prenderlo.
        completed_ids = {
            run.id for run in (store.snapshot(run_a.id), store.snapshot(run_b.id))
            if run.state == RunState.COMPLETED
        }
        assert len(completed_ids) == 1

        assert await worker_b._tick() is True
        # Anche il secondo è stato completato — da worker B ora.
        assert store.snapshot(run_a.id).state == RunState.COMPLETED
        assert store.snapshot(run_b.id).state == RunState.COMPLETED

    _run_async(scenario())
