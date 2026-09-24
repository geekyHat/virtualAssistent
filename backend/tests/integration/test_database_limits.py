"""R11: pool, statement e lock realmente limitati con ruolo applicativo."""

import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, TimeoutError

from newray.infrastructure.database import create_engine


def test_query_timeout_rollback_and_scope_reset(test_databases):
    engine = create_engine(test_databases.app, statement_timeout_ms=150, lock_timeout_ms=100)
    try:
        with pytest.raises(DBAPIError) as failure:
            with engine.begin() as conn:
                conn.execute(text("SELECT set_config('app.user_id', 'synthetic', true)"))
                conn.execute(text("SELECT pg_sleep(2)"))
        assert failure.value.orig.sqlstate == "57014"
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
            assert not conn.execute(text("SELECT current_setting('app.user_id', true)")).scalar()
            assert conn.execute(text("SHOW idle_in_transaction_session_timeout")).scalar() == "15s"
    finally:
        engine.dispose()


def test_lock_wait_is_bounded_and_connection_reusable(test_databases):
    engine = create_engine(test_databases.app, lock_timeout_ms=150)
    try:
        with engine.begin() as holder:
            holder.execute(text("SELECT pg_advisory_xact_lock(719203)"))
            with pytest.raises(DBAPIError) as failure:
                with engine.begin() as waiter:
                    waiter.execute(text("SELECT pg_advisory_xact_lock(719203)"))
            assert failure.value.orig.sqlstate == "55P03"
        with engine.begin() as conn:
            assert conn.execute(text("SELECT pg_try_advisory_xact_lock(719203)")).scalar()
    finally:
        engine.dispose()


def test_pool_exhaustion_has_no_overflow_and_finite_wait(test_databases):
    engine = create_engine(test_databases.app, pool_size=1, pool_timeout=0.1)
    try:
        with engine.connect():
            began = time.monotonic()
            with pytest.raises(TimeoutError):
                with engine.connect():
                    pytest.fail("seconda connessione oltre il limite")
            assert time.monotonic() - began < 2
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        engine.dispose()
