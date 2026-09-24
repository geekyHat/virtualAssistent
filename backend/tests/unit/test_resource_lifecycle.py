"""R11: proprietà risorse, cleanup su startup/shutdown e pool finito."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from newray.bootstrap import api
from newray.bootstrap.settings import Settings
from newray.infrastructure.database import create_engine


@pytest.mark.parametrize("failure", [None, "client", "profiles", "close"])
def test_production_lifespan_owns_one_engine_and_cleans_failures(monkeypatch, failure):
    settings = Settings(database_dsn="postgresql+psycopg://unused/unused")
    engine = MagicMock()
    client = MagicMock(aclose=AsyncMock())
    engine_factory = MagicMock(return_value=engine)
    monkeypatch.setattr(api, "Settings", lambda: settings)
    monkeypatch.setattr(api, "create_engine", engine_factory)
    client_factory = MagicMock(return_value=client)
    monkeypatch.setattr(api, "build_ollama_client", client_factory)
    identity = MagicMock()
    conversations = MagicMock()
    profiles = MagicMock()
    monkeypatch.setattr(api, "build_identity_service", identity)
    monkeypatch.setattr(api, "build_conversation_service", conversations)
    monkeypatch.setattr(api, "build_profile_service", profiles)
    if failure == "client":
        client_factory.side_effect = RuntimeError("setup")
    if failure == "profiles":
        profiles.side_effect = RuntimeError("setup")
    if failure == "close":
        client.aclose.side_effect = RuntimeError("close")

    app = api.build_app()
    engine_factory.assert_not_called()  # build/schema non allocano I/O

    async def lifecycle():
        async with app.router.lifespan_context(app):
            identity.assert_called_once_with(engine)
            conversations.assert_called_once_with(engine)
            assert profiles.call_args.args[0] is engine
            engine.dispose.assert_not_called()

    if failure:
        with pytest.raises(RuntimeError):
            asyncio.run(lifecycle())
    else:
        asyncio.run(lifecycle())
    engine_factory.assert_called_once()
    engine.dispose.assert_called_once()
    if failure != "client":
        client.aclose.assert_awaited_once()


def test_shutdown_exception_closes_injected_client():
    client = MagicMock(aclose=AsyncMock())
    app = api.create_app(MagicMock(), MagicMock(), MagicMock(), ollama_client=client)

    async def lifecycle():
        async with app.router.lifespan_context(app):
            raise RuntimeError("body")

    with pytest.raises(RuntimeError):
        asyncio.run(lifecycle())
    client.aclose.assert_awaited_once()


def test_engine_has_bounded_pool_without_connecting():
    engine = create_engine("postgresql+psycopg://unused/unused", pool_size=2, pool_timeout=0.1)
    try:
        assert engine.pool.size() == 2
        assert engine.pool.timeout() == 0.1
        assert engine.pool._max_overflow == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "field",
    [
        "database_pool_size",
        "database_pool_timeout",
        "database_connect_timeout",
        "database_statement_timeout_ms",
        "database_lock_timeout_ms",
        "database_idle_transaction_timeout_ms",
    ],
)
def test_database_limits_cannot_be_disabled(field):
    with pytest.raises(ValueError):
        Settings(database_dsn="unused", **{field: 0})


def test_lock_deadline_cannot_exceed_statement_deadline():
    with pytest.raises(ValueError):
        Settings(
            database_dsn="unused", database_statement_timeout_ms=10, database_lock_timeout_ms=20
        )
