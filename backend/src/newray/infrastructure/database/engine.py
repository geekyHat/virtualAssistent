"""Engine database (NewRay.md §§4.2, 7.3; ADR 0002).

Il ruolo applicativo connette senza superuser né BYPASSRLS: la RLS si
applica sempre, anche al proprietario delle tabelle (FORCE, migration
0001). Il contesto del principal è impostato per transazione con
``set_config(..., is_local=true)``: resta legato alla transazione e viene
pulito automaticamente a commit/rollback, senza residui nel pool.

Il bootstrap possiede un solo pool limitato per processo. I timeout PostgreSQL
limitano query, lock e transazioni inattive anche dopo la cancellazione del
consumer asincrono; non sostituiscono la deadline complessiva del futuro run.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Engine


def create_engine(
    dsn: str,
    *,
    pool_size: int = 5,
    pool_timeout: float = 3,
    connect_timeout: int = 3,
    statement_timeout_ms: int = 10_000,
    lock_timeout_ms: int = 3_000,
    idle_transaction_timeout_ms: int = 15_000,
) -> Engine:
    """Engine psycopg/SQLAlchemy per un DSN ``postgresql+psycopg://``."""
    if (
        min(
            pool_size,
            pool_timeout,
            connect_timeout,
            statement_timeout_ms,
            lock_timeout_ms,
            idle_transaction_timeout_ms,
        )
        <= 0
    ):
        raise ValueError("i limiti del database devono essere positivi")
    if lock_timeout_ms > statement_timeout_ms:
        raise ValueError("lock_timeout non può superare statement_timeout")
    return sa.create_engine(
        dsn,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=0,
        pool_timeout=pool_timeout,
        connect_args={
            "connect_timeout": connect_timeout,
            "options": (
                f"-c statement_timeout={statement_timeout_ms} "
                f"-c lock_timeout={lock_timeout_ms} "
                f"-c idle_in_transaction_session_timeout={idle_transaction_timeout_ms}"
            ),
        },
    )
