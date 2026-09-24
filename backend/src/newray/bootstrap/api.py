"""App API: route, errori e stato condiviso (NewRay.md §19).

``create_app`` accetta i servizi già composti: i test li iniettano con
i fake, la produzione usa ``build_app`` (wiring).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from newray.infrastructure.database import create_engine
from newray.infrastructure.network import HttpClient
from newray.interfaces.http.errors import install_error_handlers
from newray.interfaces.http.middleware.origin import SameOriginMiddleware
from newray.interfaces.http.routes.chat import build_router as build_chat_router
from newray.interfaces.http.routes.conversations import build_router as build_conversations_router
from newray.interfaces.http.routes.identity import build_router as build_identity_router
from newray.interfaces.http.routes.profiles import build_router as build_profiles_router
from newray.interfaces.http.routes.runs import build_router as build_runs_router
from newray.modules.conversations import ConversationService
from newray.modules.identity import IdentityService
from newray.modules.models import ChatModel
from newray.modules.profiles import ProfileService
from newray.modules.runs import DurableRunService, InlineRunService, RunStore

from .settings import Settings
from .wiring import (
    build_chat_model,
    build_conversation_service,
    build_durable_run_service,
    build_identity_service,
    build_model_catalog,
    build_ollama_client,
    build_profile_service,
)


def create_app(
    identity: IdentityService,
    conversations: ConversationService,
    profiles: ProfileService,
    cookie_secure: bool = False,
    ollama_client: HttpClient | None = None,
    chat_model: ChatModel | None = None,
    *,
    public_origin: str = "http://testserver",
    run_store: RunStore | None = None,
    runs_max_queue_depth: int = 50,
) -> FastAPI:
    """Applicazione FastAPI attorno ai servizi iniettati.

    ``ollama_client`` è il client di egress verso il runtime (B-03): se
    presente viene chiuso in modo ordinato alla dismissione dell'app.
    ``run_store`` abilita la route dei run durevoli (P-05) quando fornito
    insieme a ``chat_model``; nei test un fake basta a coprire il contratto.
    """

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if ollama_client is not None:
                await ollama_client.aclose()

    app = _http_app(lifespan, cookie_secure, public_origin)
    app.state.identity_service = identity
    app.state.conversation_service = conversations
    app.state.profile_service = profiles
    if chat_model is not None:
        inline = InlineRunService(conversations, profiles, chat_model)
        app.state.inline_run_service = inline
        if run_store is not None:
            app.state.durable_run_service = DurableRunService(
                run_store, inline, max_queue_depth=runs_max_queue_depth
            )
    return app


def _http_app(
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]],
    cookie_secure: bool,
    public_origin: str,
) -> FastAPI:
    app = FastAPI(title="NewRay", version="0.1.0", lifespan=lifespan)
    app.add_middleware(SameOriginMiddleware, public_origin=public_origin)
    install_error_handlers(app)
    app.include_router(build_identity_router(cookie_secure))
    app.include_router(build_conversations_router())
    app.include_router(build_profiles_router())
    app.include_router(build_chat_router())
    app.include_router(build_runs_router())
    return app


def build_app() -> FastAPI:
    """Composizione di produzione: settings dall'ambiente, PostgreSQL reale."""
    # Settings() legge le variabili NEWRAY_*: mypy non vede le fonti
    # dinamiche di pydantic-settings, da qui l'ignore mirato.
    settings = Settings()  # type: ignore[call-arg]
    # Il validator di Settings materializza sempre l'origine canonica.
    assert settings.public_origin is not None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Allocazione dentro il lifespan: nessuna risorsa aperta da build_app
        # o dalla generazione OpenAPI. ExitStack pulisce anche setup falliti.
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
            ollama_client = build_ollama_client(settings)
            if ollama_client is not None:
                resources.push_async_callback(ollama_client.aclose)
            app.state.identity_service = build_identity_service(engine)
            app.state.conversation_service = build_conversation_service(engine)
            catalog = build_model_catalog(ollama_client)
            app.state.profile_service = build_profile_service(engine, settings, catalog)
            inline = InlineRunService(
                app.state.conversation_service,
                app.state.profile_service,
                build_chat_model(ollama_client, settings),
            )
            app.state.inline_run_service = inline
            app.state.durable_run_service = build_durable_run_service(engine, inline, settings)
            yield

    return _http_app(lifespan, settings.cookie_secure, settings.public_origin)
