"""Entrypoint del worker P-05: processo separato dall'API.

Un solo worker per macchina in questa slice; garantisce l'invariante "una
sola inference generativa attiva" senza serializzazione OS-level (che
resta a P-18). Il fencing atomico protegge contro processi zombie del
worker precedente.

Il segnale SIGTERM chiude il worker in modo ordinato: il run in corso
resta con lease valido; alla riacquisizione dopo TTL un nuovo worker
riprende partendo dallo snapshot.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import AsyncExitStack

from newray.infrastructure.database import create_engine
from newray.kernel.clock import SystemClock
from newray.modules.conversations import ConversationService
from newray.modules.conversations.adapters.postgres import (
    PostgresConversationRepository,
    PostgresMessageStore,
)
from newray.modules.runs import DurableRunWorker
from newray.modules.runs.adapters.postgres import PostgresRunStore

from .settings import Settings
from .wiring import build_chat_model, build_ollama_client

logger = logging.getLogger("newray.worker")


async def _run() -> None:
    settings = Settings()  # type: ignore[call-arg]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    async with AsyncExitStack() as resources:
        engine = create_engine(
            settings.database_dsn,
            pool_size=settings.database_pool_size,
            pool_timeout=settings.database_pool_timeout,
            connect_timeout=settings.database_connect_timeout,
            statement_timeout_ms=settings.database_statement_timeout_ms,
            lock_timeout_ms=settings.database_lock_timeout_ms,
            idle_transaction_timeout_ms=settings.database_idle_transaction_timeout_ms,
        )
        resources.push_async_callback(asyncio.to_thread, engine.dispose)
        client = build_ollama_client(settings)
        if client is not None:
            resources.push_async_callback(client.aclose)
        chat_model = build_chat_model(client, settings)
        conversations = ConversationService(
            PostgresConversationRepository(engine),
            PostgresMessageStore(engine),
            SystemClock(),
        )
        worker = DurableRunWorker(
            store=PostgresRunStore(engine),
            chat_model=chat_model,
            conversations=conversations,
        )
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, worker.stop)
        await worker.run_forever()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
