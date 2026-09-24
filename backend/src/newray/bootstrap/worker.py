"""Entry point del worker dei run durevoli (P-05, NewRay.md §5, §8.4).

Avviato/riusato/fermato dal launcher (``scripts/start_local.py``), come
processo separato dall'API: stesso engine/ruolo applicativo, stesso
``ChatModel``, stessi casi d'uso (ADR 0001). ``python -m newray.bootstrap.worker``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket

from newray.infrastructure.database import create_engine
from newray.modules.runs import RunWorker

from .settings import Settings
from .wiring import (
    build_chat_model,
    build_conversation_service,
    build_ollama_client,
    build_run_store,
)

logger = logging.getLogger("newray.worker")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()  # type: ignore[call-arg]
    engine = create_engine(
        settings.database_dsn,
        pool_size=settings.database_pool_size,
        pool_timeout=settings.database_pool_timeout,
        connect_timeout=settings.database_connect_timeout,
        statement_timeout_ms=settings.database_statement_timeout_ms,
        lock_timeout_ms=settings.database_lock_timeout_ms,
        idle_transaction_timeout_ms=settings.database_idle_transaction_timeout_ms,
    )
    ollama_client = build_ollama_client(settings)
    try:
        conversations = build_conversation_service(engine)
        chat_model = build_chat_model(ollama_client, settings)
        worker = RunWorker(
            build_run_store(engine),
            conversations,
            chat_model,
            max_duration_seconds=settings.run_max_duration_seconds,
        )
        await worker.register(pid=os.getpid(), hostname=socket.gethostname())
        logger.info("worker %s pronto (risorsa %s)", worker.worker_id, worker.resource_id)

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _request_stop() -> None:
            logger.info("arresto richiesto: termino il run in corso, poi esco")
            stop.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _request_stop)

        await worker.run_forever(stop)
    finally:
        if ollama_client is not None:
            await ollama_client.aclose()
        await asyncio.to_thread(engine.dispose)


if __name__ == "__main__":
    asyncio.run(main())
