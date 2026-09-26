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
from newray.modules.models import ChatModel, HardwareFingerprint, ModelCatalog
from newray.modules.models.adapters.empty import EmptyModelCatalog, UnavailableChatModel
from newray.modules.models.adapters.ollama import OllamaChatModel, OllamaModelCatalog
from newray.modules.models.adapters.qualification_file import FileQualificationStore
from newray.modules.models.adapters.qualified_catalog import QualifiedModelCatalog
from newray.modules.profiles import ProfileService
from newray.modules.profiles.adapters.postgres import (
    PostgresModelBindingStore,
    PostgresProfileDefaultsSeeder,
    PostgresProfileRepository,
    PostgresProfileVersionWriter,
)
from newray.modules.runs import (
    ChatModelRunExecutor,
    DurableRunService,
    InlineRunService,
    RunLauncher,
    Worker,
)
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


def build_qualification_store(settings: Settings) -> FileQualificationStore:
    """Store file-based della qualifica (P-19).

    Ogni host ha la sua qualifica in ``settings.data_dir / "qualifications"``:
    nessun scope (§9.1) — la qualifica è uno stato del runtime, non un dato
    privato dell'utente.
    """
    return FileQualificationStore(settings.data_dir / "qualifications")


def wrap_catalog_with_qualification(
    base: ModelCatalog,
    store: FileQualificationStore,
    hardware: HardwareFingerprint | None,
    runtime: str = "ollama",
) -> ModelCatalog:
    """Aggiunge le capacità qualificate al ``readiness``/``list_models`` (P-19).

    Il wrapper non concede ``QUALIFIED``: si limita a mostrare le capacità
    dimostrate dalla campagna per digest/runtime/hardware correnti. Su
    mismatch le capacità dichiarate restano ma quelle qualificate sono ``()``.
    """
    return QualifiedModelCatalog(base, store, hardware, runtime)


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


def build_durable_run_service(
    engine: Engine,
    inline: InlineRunService,
) -> DurableRunService:
    """Casi d'uso dei run durevoli su PostgreSQL, ricevuta idempotente scoped."""
    return DurableRunService(PostgresRunStore(engine), inline)


def build_run_event_reader(engine: Engine) -> PostgresRunStore:
    """Lettore della outbox eventi (P-06): condivide l'engine con lo store."""
    return PostgresRunStore(engine)


def build_run_launcher(
    engine: Engine,
    chat_model: ChatModel,
    settings: Settings,
) -> RunLauncher:
    """Un solo :class:`Worker` per il pilot; ownership esplicita del lifespan.

    Il pilot ha un unico binding generativo attivo (§P-05, ADR 0006), quindi
    un launcher/worker è sufficiente. Un ampliamento a più binding entrerà
    quando saranno reintrodotti profili aggiuntivi (post-pilot).
    """
    worker = Worker(
        PostgresRunStore(engine),
        ChatModelRunExecutor(
            chat_model,
            max_wall_seconds=settings.run_max_wall_seconds,
        ),
        SystemClock(),
        lease_duration_seconds=settings.run_lease_duration_seconds,
        heartbeat_seconds=settings.run_heartbeat_seconds,
    )
    return RunLauncher(
        worker,
        idle_backoff_seconds=settings.run_idle_backoff_seconds,
    )
