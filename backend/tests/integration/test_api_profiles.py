"""Percorso completo API → caso d'uso → dati, profili (B-02).

Usa il ruolo applicativo ``newray_app`` del database di test fresco;
senza i DSN ``NEWRAY_TEST_*_URL`` i test si saltano (suite offline).
Includono il secondo principal: la sua visibilità è nulla anche via API.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime

import httpx
import pytest
from conftest import DatabaseHandles
from fastapi.testclient import TestClient
from sqlalchemy import text

from fakes import InMemoryModelCatalog
from newray.bootstrap.api import create_app
from newray.infrastructure.database import create_engine
from newray.kernel.clock import SystemClock
from newray.kernel.errors import Conflict
from newray.kernel.identity import Role, new_id
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
from newray.modules.identity.domain import User
from newray.modules.models import RUNTIME_OLLAMA, ModelInfo, ModelStatus
from newray.modules.models.adapters.empty import EmptyModelCatalog
from newray.modules.profiles import ModelBinding, ProfileService, ProfileVersion
from newray.modules.profiles.adapters.postgres import (
    PostgresModelBindingStore,
    PostgresProfileDefaultsSeeder,
    PostgresProfileRepository,
    PostgresProfileVersionWriter,
)

MODEL = ModelInfo(
    name="llama3.1",
    runtime=RUNTIME_OLLAMA,
    digest="sha256:abc123",
    status=ModelStatus.QUALIFIED,
    capabilities=("chat",),
)

OTHER_MODEL = ModelInfo(
    name="newray-gemma4-31b-it:ud-q4-k-xl",
    runtime=RUNTIME_OLLAMA,
    digest="sha256:def456",
    status=ModelStatus.INSTALLED,
    capabilities=("chat",),
)


def _now() -> datetime:
    return datetime.now(UTC)


def test_lock_postgres_non_blocca_il_catalogo_http(app_setup, test_databases, monkeypatch):
    identity, conversations, profiles, owner, _ = app_setup
    profiles.ensure_defaults(owner)
    profile = asyncio.run(profiles.list_profiles(owner))[0].profile
    engine = create_engine(test_databases.app)
    entered = asyncio.Event()
    original = profiles._profiles.get_profile

    async def scenario():
        loop = asyncio.get_running_loop()

        def get_with_lock(scope, profile_id):
            with engine.begin() as conn:
                loop.call_soon_threadsafe(entered.set)
                conn.execute(text("SELECT pg_advisory_xact_lock(719204)"))
            return original(scope, profile_id)

        monkeypatch.setattr(profiles._profiles, "get_profile", get_with_lock)
        app = create_app(identity, conversations, profiles)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            cookies={"newray_session": str(owner.session_id)},
        ) as client:
            holder = engine.connect()
            try:
                holder.execute(text("SELECT pg_advisory_xact_lock(719204)"))
                pending = asyncio.create_task(client.get(f"/api/v1/profiles/{profile.id}/binding"))
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    response = await asyncio.wait_for(client.get("/api/v1/models"), 1)
                    assert response.status_code == 200
                    assert not pending.done()
                finally:
                    holder.rollback()
                # Il catalogo del test è intenzionalmente non configurato.
                assert (await pending).status_code == 503
            finally:
                holder.close()

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


@pytest.fixture()
def app_setup(test_databases: DatabaseHandles):
    """App su PostgreSQL reale, bootstrap del proprietario A, sessione B."""
    engine = create_engine(test_databases.app)
    identity = IdentityService(
        PostgresUserRepository(engine),
        PostgresSessionStore(engine),
        SystemClock(),
        PostgresOwnerBootstrap(engine),
    )
    conversations = ConversationService(
        PostgresConversationRepository(engine), PostgresMessageStore(engine), SystemClock()
    )
    profiles = ProfileService(
        PostgresProfileRepository(engine),
        PostgresModelBindingStore(engine),
        EmptyModelCatalog(),
        SystemClock(),
        PostgresProfileDefaultsSeeder(engine),
        default_model_name="llama3.1",
    )
    owner = identity.bootstrap_owner("Ada", "test-passphrase-1234")
    b = User(new_id(), owner.organization_id, "B", Role.MEMBER, _now())
    assert PostgresUserRepository(engine).add_user(b) is True
    session_b = identity.create_session(b)
    yield identity, conversations, profiles, owner, session_b
    engine.dispose()


@pytest.fixture()
def client(app_setup) -> TestClient:
    identity, conversations, profiles, owner, _ = app_setup
    test_client = TestClient(
        create_app(identity, conversations, profiles, cookie_secure=False),
        headers={"Origin": "http://testserver"},
    )
    test_client.cookies.set("newray_session", str(owner.session_id))
    return test_client


@pytest.fixture()
def client_b(app_setup) -> TestClient:
    identity, conversations, profiles, _, session_b = app_setup
    test_client = TestClient(
        create_app(identity, conversations, profiles, cookie_secure=False),
        headers={"Origin": "http://testserver"},
    )
    test_client.cookies.set("newray_session", str(session_b.id))
    return test_client


@pytest.fixture()
def app_setup_with_models(test_databases: DatabaseHandles):
    """App su PostgreSQL reale con due modelli nel catalogo, per lo switch."""
    engine = create_engine(test_databases.app)
    identity = IdentityService(
        PostgresUserRepository(engine),
        PostgresSessionStore(engine),
        SystemClock(),
        PostgresOwnerBootstrap(engine),
    )
    conversations = ConversationService(
        PostgresConversationRepository(engine), PostgresMessageStore(engine), SystemClock()
    )
    profiles = ProfileService(
        PostgresProfileRepository(engine),
        PostgresModelBindingStore(engine),
        InMemoryModelCatalog((MODEL, OTHER_MODEL)),
        SystemClock(),
        PostgresProfileDefaultsSeeder(engine),
        default_model_name=MODEL.name,
        version_writer=PostgresProfileVersionWriter(engine),
    )
    owner = identity.bootstrap_owner("Ada", "test-passphrase-1234")
    profiles.ensure_defaults(owner)
    b = User(new_id(), owner.organization_id, "B", Role.MEMBER, _now())
    assert PostgresUserRepository(engine).add_user(b) is True
    session_b = identity.create_session(b)
    yield identity, conversations, profiles, owner, session_b
    engine.dispose()


@pytest.fixture()
def client_with_models(app_setup_with_models) -> TestClient:
    identity, conversations, profiles, owner, _ = app_setup_with_models
    test_client = TestClient(
        create_app(identity, conversations, profiles, cookie_secure=False),
        headers={"Origin": "http://testserver"},
    )
    test_client.cookies.set("newray_session", str(owner.session_id))
    return test_client


@pytest.fixture()
def client_with_models_b(app_setup_with_models) -> TestClient:
    identity, conversations, profiles, _, session_b = app_setup_with_models
    test_client = TestClient(
        create_app(identity, conversations, profiles, cookie_secure=False),
        headers={"Origin": "http://testserver"},
    )
    test_client.cookies.set("newray_session", str(session_b.id))
    return test_client


def test_percorso_completo_profilo(client: TestClient) -> None:
    # GET puro; POST esplicito crea il solo Assistente pilot.
    response = client.get("/api/v1/profiles")
    assert response.status_code == 200
    assert response.json()["items"] == []
    created = client.post("/api/v1/profiles/defaults")
    assert created.status_code == 200
    assert created.json()["kind"] == "assistant"
    assert client.post("/api/v1/profiles/defaults").json()["id"] == created.json()["id"]
    response = client.get("/api/v1/profiles")
    items = response.json()["items"]
    assert len(items) == 1
    assert {item["kind"] for item in items} == {"assistant"}
    assert all(item["model"] is None for item in items)

    # Senza runtime collegato (B-03) il catalogo è vuoto, in modo onesto.
    assert client.get("/api/v1/models").json() == {"items": []}

    # Risoluzione senza modello installato: 503 recuperabile, mai un
    # fallback invisibile (ADR 0003).
    profile_id = items[0]["id"]
    response = client.get(f"/api/v1/profiles/{profile_id}/binding")
    assert response.status_code == 503
    payload = response.json()
    assert payload["code"] == "MODEL_UNAVAILABLE"
    assert payload["retryable"] is False

    # Il profilo sconosciuto risponde 404 uniforme.
    assert client.get(f"/api/v1/profiles/{new_id()}/binding").status_code == 404


def test_secondo_principal_ve_i_propri_profili(client: TestClient, client_b: TestClient) -> None:
    assert client.post("/api/v1/profiles/defaults").status_code == 200
    assert client_b.post("/api/v1/profiles/defaults").status_code == 200
    a_items = client.get("/api/v1/profiles").json()["items"]
    b_items = client_b.get("/api/v1/profiles").json()["items"]
    assert len(a_items) == 1 and len(b_items) == 1
    a_ids = {item["id"] for item in a_items}
    b_ids = {item["id"] for item in b_items}
    assert a_ids.isdisjoint(b_ids)
    # B non risolve i profili di A: 404, senza rivelarne l'esistenza.
    for a_id in a_ids:
        assert client_b.get(f"/api/v1/profiles/{a_id}/binding").status_code == 404


def test_switch_model_persiste_ed_è_visibile_dopo_reload(client_with_models: TestClient) -> None:
    profile = client_with_models.get("/api/v1/profiles").json()["items"][0]
    response = client_with_models.post(
        f"/api/v1/profiles/{profile['id']}/versions",
        json={
            "model_name": OTHER_MODEL.name,
            "expected_profile_version": profile["version"],
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["binding"]["model_name"] == OTHER_MODEL.name
    assert payload["version"] != profile["version"]

    # Un nuovo ProfileService sullo stesso database legge la stessa versione:
    # la scrittura è su PostgreSQL, non solo nell'oggetto in memoria.
    reloaded = client_with_models.get("/api/v1/profiles").json()["items"]
    current = next(p for p in reloaded if p["id"] == profile["id"])
    assert current["version"] == payload["version"]
    assert current["binding"]["model_name"] == OTHER_MODEL.name
    assert current["model"]["name"] == OTHER_MODEL.name


def test_switch_model_versione_obsoleta_409(client_with_models: TestClient) -> None:
    profile = client_with_models.get("/api/v1/profiles").json()["items"][0]
    response = client_with_models.post(
        f"/api/v1/profiles/{profile['id']}/versions",
        json={"model_name": OTHER_MODEL.name, "expected_profile_version": "9.9.9"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


def test_switch_model_secondo_principal_non_tocca_il_profilo_di_a(
    client_with_models: TestClient, client_with_models_b: TestClient
) -> None:
    a_profile = client_with_models.get("/api/v1/profiles").json()["items"][0]
    # B non vede il profilo di A: 404 uniforme, non rivela l'esistenza.
    response = client_with_models_b.post(
        f"/api/v1/profiles/{a_profile['id']}/versions",
        json={"model_name": OTHER_MODEL.name},
    )
    assert response.status_code == 404
    # Il profilo di A resta sul modello originale.
    reloaded = client_with_models.get("/api/v1/profiles").json()["items"]
    current = next(p for p in reloaded if p["id"] == a_profile["id"])
    assert current["version"] == a_profile["version"]


def test_switch_model_due_scritture_concorrenti_una_sola_vince(
    app_setup_with_models, test_databases: DatabaseHandles
) -> None:
    """Prova reale del vincolo UNIQUE come barriera di concorrenza: senza
    grant UPDATE su ``profile_versions`` (0003), due switch paralleli con
    la stessa versione attesa non possono serializzarsi con un lock di
    riga — la seconda scrittura deve perdere sulla violazione del vincolo,
    mai sovrascrivere silenziosamente la prima."""
    _identity, _conversations, profiles, owner, _session_b = app_setup_with_models
    profile = asyncio.run(profiles.list_profiles(owner))[0].profile
    engine = create_engine(test_databases.app)
    writer = PostgresProfileVersionWriter(engine)
    ready = threading.Barrier(2)
    results: list[object] = []
    lock = threading.Lock()

    def attempt(model_name: str) -> None:
        binding_id = new_id()
        binding = ModelBinding(
            id=binding_id,
            organization_id=owner.organization_id,
            owner_id=owner.user_id,
            name=str(new_id()),
            runtime=RUNTIME_OLLAMA,
            model_name=model_name,
            parameters={},
            created_at=_now(),
        )
        version = ProfileVersion(
            id=new_id(),
            profile_id=profile.id,
            organization_id=owner.organization_id,
            owner_id=owner.user_id,
            version="1.0.1",
            model_binding_id=binding_id,
            instructions="",
            created_at=_now(),
        )
        ready.wait(timeout=5)
        try:
            outcome = writer.switch_model(
                owner.scope,
                profile.id,
                binding,
                version,
                expected_profile_version="1.0.0",
                idempotency_key=None,
                request_hash=None,
            )
        except Conflict as exc:
            outcome = exc
        with lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=attempt, args=(OTHER_MODEL.name,)),
        threading.Thread(target=attempt, args=(MODEL.name,)),
    ]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
    finally:
        engine.dispose()

    conflicts = [r for r in results if isinstance(r, Conflict)]
    successes = [r for r in results if not isinstance(r, Conflict)]
    assert len(successes) == 1, results
    assert len(conflicts) == 1, results
