"""Worker durevole della coda dei run (NewRay.md §8, P-05).

Il worker consuma dalla tabella ``runs`` con claim atomico e lease/fence:
solo il worker che detiene la generazione corrente può persistere partial
o esito. Un lease scaduto viene rilevato al claim successivo, che bumpa
``fence`` e invalida ogni finalize del vecchio worker.

Questa slice non collega ancora l'esecutore reale (Ollama/Gemma): definisce
il contratto ``RunExecutor`` e la meccanica di ciclo. L'adapter di
inference entra nelle slice successive di P-05/P-07.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from newray.kernel.clock import Clock
from newray.kernel.identity import new_id

from .durable import ClaimedRun, DurableRun, RunState, RunStore

log = logging.getLogger(__name__)


class LeaseLost(Exception):
    """Il worker ha perso il lease durante l'esecuzione: fermare subito."""


class CancelRequested(Exception):
    """L'utente ha chiesto lo stop: interrompere l'executor.

    Il worker cattura questa eccezione e finalizza il run con stato
    ``CANCELLED``. Non è un errore: è la via ordinaria della
    cancellazione cooperativa (§P-06). ``partial_text`` è l'ultimo
    testo persistito dal checkpoint prima della richiesta.
    """

    def __init__(self, message: str, *, partial_text: str = "") -> None:
        super().__init__(message)
        self.partial_text = partial_text


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Esito che il worker consegna al finalize.

    ``state`` è terminale; il chiamante è responsabile della coerenza con
    ``finish_reason`` (§8.3).
    """

    state: RunState
    finish_reason: str | None
    partial_text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    eval_duration_ns: int | None = None


class PartialCheckpoint(Protocol):
    """Persiste testo parziale se il lease è ancora vivo.

    L'implementazione lancia :class:`LeaseLost` quando il fence non
    corrisponde più: l'esecutore deve interrompere subito, senza
    produrre ulteriore output né ritentare. Solleva :class:`CancelRequested`
    quando l'utente ha chiesto lo stop: l'esecutore chiude anch'esso,
    ma il worker finalizza in ``CANCELLED`` (via ordinaria, non errore).
    """

    async def save(self, delta_text: str, partial_text: str) -> None:
        """``delta_text`` è il frammento appena prodotto; ``partial_text``
        è l'accumulato totale che il reducer usa come snapshot iniziale
        su reconnect (P-06). Entrambi finiscono in un unico evento
        ``delta`` sotto la stessa transazione dello stato."""
        ...


class RunExecutor(Protocol):
    """Contratto minimo del motore generativo (nessun tool in P-05).

    L'implementazione consuma :attr:`DurableRun.snapshot` (input, binding,
    contesto congelato) e produce un :class:`RunOutcome`. La chiamata
    riceve un :class:`PartialCheckpoint` per persistenza incrementale.
    """

    async def execute(
        self,
        run: DurableRun,
        checkpoint: PartialCheckpoint,
    ) -> RunOutcome: ...


class _StoreCheckpoint:
    """Adapter :class:`PartialCheckpoint` sopra il :class:`RunStore`.

    Persistenza sincrona offloadata: l'executor await tra un delta e
    l'altro; nessuna transazione DB resta aperta durante inference.
    """

    def __init__(self, worker: Worker, claimed: ClaimedRun) -> None:
        self._worker = worker
        self._claimed = claimed

    async def save(self, delta_text: str, partial_text: str) -> None:
        ok = await asyncio.to_thread(
            self._worker._store.save_partial,
            self._claimed.run.id,
            self._claimed.worker_id,
            self._claimed.fence,
            partial_text,
            delta_text,
            self._worker._clock.now(),
        )
        if not ok:
            raise LeaseLost(f"lease perso su run {self._claimed.run.id}")
        # Cancellazione cooperativa: dopo il checkpoint del delta,
        # controlliamo il flag persistito. È una lettura leggera; non
        # riapre la transazione precedente.
        cancel = await asyncio.to_thread(
            self._worker._store.is_cancel_requested,
            self._claimed.worker_id,
            self._claimed.run.id,
            self._claimed.fence,
        )
        if cancel:
            raise CancelRequested(
                f"cancellazione richiesta su run {self._claimed.run.id}",
                partial_text=partial_text,
            )


class Worker:
    """Loop di consumo con heartbeat e finalize sotto fence.

    Un'istanza rappresenta un singolo consumatore (ownership esplicita):
    il launcher del pilot ne avvia una per risorsa generativa. Il worker
    non fissa GPU: rifiuta un run se ``executor`` solleva.
    """

    def __init__(
        self,
        store: RunStore,
        executor: RunExecutor,
        clock: Clock,
        *,
        worker_id: uuid.UUID | None = None,
        lease_duration_seconds: int = 30,
        heartbeat_seconds: float = 10.0,
    ) -> None:
        if lease_duration_seconds <= 0:
            raise ValueError("lease_duration_seconds deve essere > 0")
        if heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds deve essere > 0")
        if heartbeat_seconds >= lease_duration_seconds:
            raise ValueError("heartbeat_seconds deve essere < lease_duration_seconds")
        self._store = store
        self._executor = executor
        self._clock = clock
        self._worker_id = worker_id or new_id()
        self._lease_duration_seconds = lease_duration_seconds
        self._heartbeat_seconds = heartbeat_seconds

    @property
    def worker_id(self) -> uuid.UUID:
        return self._worker_id

    async def poll_once(self) -> bool:
        """Prova a consumare un run. Ritorna True se ne ha preso uno."""
        claimed = await asyncio.to_thread(
            self._store.claim,
            self._worker_id,
            self._lease_duration_seconds,
            self._clock.now(),
        )
        if claimed is None:
            return False
        await self._process(claimed)
        return True

    async def run_forever(
        self,
        *,
        idle_backoff_seconds: float = 1.0,
        should_stop: Callable[[], bool] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Loop di consumo con backoff quando la coda è vuota.

        ``should_stop`` è polled tra un claim e l'altro: il launcher può
        chiedere l'uscita cooperativa senza killare un run in corso.
        """
        while True:
            if should_stop is not None and should_stop():
                return
            took = await self.poll_once()
            if not took:
                await sleep(idle_backoff_seconds)

    async def _process(self, claimed: ClaimedRun) -> None:
        checkpoint = _StoreCheckpoint(self, claimed)
        heartbeat = asyncio.create_task(self._heartbeat(claimed))
        try:
            try:
                outcome = await self._executor.execute(claimed.run, checkpoint)
            except LeaseLost:
                log.warning("lease perso durante execute su run %s", claimed.run.id)
                return
            except CancelRequested as exc:
                # Via ordinaria della cancellazione: il partial è quello
                # appena persistito dal checkpoint (portato dall'exc).
                outcome = RunOutcome(
                    state=RunState.CANCELLED,
                    finish_reason="cancelled",
                    partial_text=exc.partial_text,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("executor ha sollevato su run %s", claimed.run.id)
                outcome = RunOutcome(
                    state=RunState.FAILED,
                    finish_reason=f"executor_error: {type(exc).__name__}",
                    partial_text=claimed.run.partial_text,
                )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        ok = await asyncio.to_thread(
            self._store.finalize,
            claimed.run.id,
            claimed.worker_id,
            claimed.fence,
            outcome.state,
            outcome.finish_reason,
            outcome.partial_text,
            outcome.prompt_tokens,
            outcome.completion_tokens,
            outcome.eval_duration_ns,
            self._clock.now(),
        )
        if not ok:
            log.warning(
                "finalize rifiutato per fence obsoleto su run %s (worker %s, fence %d)",
                claimed.run.id,
                claimed.worker_id,
                claimed.fence,
            )

    async def _heartbeat(self, claimed: ClaimedRun) -> None:
        """Rinnova il lease finché non è più nostro; solleva senza pretesa."""
        try:
            while True:
                await asyncio.sleep(self._heartbeat_seconds)
                now = self._clock.now()
                from datetime import timedelta

                lease_until = now + timedelta(seconds=self._lease_duration_seconds)
                ok = await asyncio.to_thread(
                    self._store.renew_lease,
                    claimed.run.id,
                    claimed.worker_id,
                    claimed.fence,
                    lease_until,
                    now,
                )
                if not ok:
                    log.warning(
                        "renew_lease rifiutato su run %s: fence obsoleto",
                        claimed.run.id,
                    )
                    return
        except asyncio.CancelledError:
            raise
