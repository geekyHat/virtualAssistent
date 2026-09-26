"""Launcher del worker durevole (P-05).

Il launcher possiede il ciclo di vita di un singolo :class:`Worker`:
- lo avvia su un task asincrono con ownership esplicita e readiness;
- espone ``start``/``stop`` cooperativi legati al lifespan dell'app;
- non moltiplica worker sulla stessa risorsa generativa (una inference
  attiva per binding; §P-05).

Non ricicla GPU per TTL: interrompe il worker in modo cooperativo (via
``should_stop``) e attende la fine del task in corso. Il fencing sul
lease impedisce comunque a un worker precedente di finalizzare se
qualcuno lo forza offline.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .worker import Worker

log = logging.getLogger(__name__)


class RunLauncher:
    """Owner cooperativo di un :class:`Worker` durante il lifespan API."""

    def __init__(
        self,
        worker: Worker,
        *,
        idle_backoff_seconds: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if idle_backoff_seconds <= 0:
            raise ValueError("idle_backoff_seconds deve essere > 0")
        self._worker = worker
        self._idle_backoff_seconds = idle_backoff_seconds
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def worker(self) -> Worker:
        return self._worker

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("launcher già avviato")
        self._stop.clear()
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run(), name="newray-run-worker")

    async def stop(self, *, timeout: float | None = None) -> None:
        """Chiede la fermata cooperativa e attende la fine del task."""
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=timeout)
        except TimeoutError:
            log.warning("worker %s non ha completato lo stop nel timeout", self._worker.worker_id)
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        finally:
            self._task = None

    async def _run(self) -> None:
        try:
            await self._worker.run_forever(
                idle_backoff_seconds=self._idle_backoff_seconds,
                should_stop=self._stop.is_set,
                sleep=self._sleep,
            )
        except Exception:  # noqa: BLE001
            log.exception("worker %s è terminato con errore", self._worker.worker_id)


__all__ = ["RunLauncher"]
