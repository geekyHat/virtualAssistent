"""Unico punto di composizione dell'applicazione (NewRay.md §5)."""

from __future__ import annotations

from sqlalchemy.engine import Engine

from newray.infrastructure.network import HttpClient
from newray.kernel.clock import SystemClock
from newray.modules.conversations import ConversationService
from newray.modules.conversations.adapters.postgres import (
    PostgresConversationRepository,
    PostgresMessageStore,
)
from newray.modules.identity import IdentityService
from newray.modules.identity.adapters.postgres import (
    PostgresOwnerBootstrap,
    PostgresSessionStore,
    PostgresUserRepository,
)
from newray.modules.models import ChatModel, ModelCatalog
from newray.modules.models.adapters.empty import EmptyModelCatalog, UnavailableChatModel
from newray.modules.models.adapters.ollama import OllamaChatModel, OllamaModelCatalog
from newray.modules.profiles import ProfileService
from newray.modules.profiles.adapters.postgres import (
    PostgresModelBindingStore,
    PostgresProfileDefaultsSeeder,
    PostgresProfileRepository,
    PostgresProfileVersionWriter,
)
from newray.modules.runs import DurableRunService, InlineRunService, RunStore
from newray.modules.runs.adapters.postgres import PostgresRunStore

from .settings import Settings


def build_identity_service(engine: Engine) -> IdentityService:
    """Casi d'uso identità su PostgreSQL, con il ruolo applicativo."""
    return IdentityService(
        PostgresUserRepository(engine),
        PostgresSessionStore(engine),
        SystemClock(),
        PostgresOwnerBootstrap(engine),
    )


def build_conversation_service(engine: Engine) -> ConversationService:
    """Casi d'uso conversazioni su PostgreSQL, con il ruolo applicativo."""
    return ConversationService(
        PostgresConversationRepository(engine),
        PostgresMessageStore(engine),
        SystemClock(),
    )


def build_ollama_client(settings: Settings) -> HttpClient | None:
    """Client di egress controllato verso il runtime Ollama (B-03).

    ``NEWRAY_OLLAMA_BASE_URL`` assente → nessun runtime collegato:
    catalogo vuoto e ``MODEL_UNAVAILABLE`` recuperabile (ADR 0003),
    nessuna inference.
    """
    if settings.ollama_base_url is None:
        return None
    return HttpClient(settings.ollama_base_url)


def build_chat_model(client: HttpClient | None, settings: Settings | None = None) -> ChatModel:
    """Modello di chat: Ollama se collegato, errore esplicito altrimenti."""
    if client is None:
        return UnavailableChatModel()
    if settings is None:
        return OllamaChatModel(client)
    return OllamaChatModel(
        client,
        context_length=settings.model_context_length,
        max_context_length=settings.model_max_context_length,
    )


def build_model_catalog(client: HttpClient | None) -> ModelCatalog:
    """Catalogo dei modelli: stato reale se Ollama è collegato, vuoto no."""
    if client is None:
        return EmptyModelCatalog()
    return OllamaModelCatalog(client)


def build_profile_service(
    engine: Engine, settings: Settings, catalog: ModelCatalog
) -> ProfileService:
    """Casi d'uso profili su PostgreSQL, con il ruolo applicativo.

    Il catalogo viene composto nel bootstrap: vuoto finché il runtime
    Ollama non è collegato, la risoluzione risponde ``MODEL_UNAVAILABLE``
    recuperabile (ADR 0003).
    """
    return ProfileService(
        PostgresProfileRepository(engine),
        PostgresModelBindingStore(engine),
        catalog,
        SystemClock(),
        PostgresProfileDefaultsSeeder(engine),
        default_model_name=settings.default_model_name,
        version_writer=PostgresProfileVersionWriter(engine),
    )


def build_run_store(engine: Engine) -> RunStore:
    """Adapter Postgres del run store (P-05), con il ruolo applicativo."""
    return PostgresRunStore(engine)


def build_durable_run_service(
    engine: Engine,
    inline: InlineRunService,
    settings: Settings,
) -> DurableRunService:
    """Casi d'uso dei run durevoli: creazione idempotente e lettura scoped."""
    return DurableRunService(
        build_run_store(engine),
        inline,
        max_queue_depth=settings.runs_max_queue_depth,
    )
