"""Contratti HTTP di profili e modelli (NewRay.md §19.2, §19.4; B-02).

I fake verificano il contratto (NewRay.md §22.1): stati, forma dei
payload, codici di errore stabili, ``MODEL_UNAVAILABLE`` recuperabile.
La prova su PostgreSQL reale è in ``tests/integration``.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from fakes import (
    FakeClock,
    InMemoryConversationRepository,
    InMemoryMessageStore,
    InMemoryModelBindingStore,
    InMemoryModelCatalog,
    InMemoryOwnerBootstrap,
    InMemoryProfileDefaultsSeeder,
    InMemoryProfileRepository,
    InMemoryProfileVersionWriter,
    InMemorySessionStore,
    InMemoryUserRepository,
)
from newray.bootstrap.api import create_app
from newray.interfaces.http.middleware.identity import require_principal
from newray.modules.conversations import ConversationService
from newray.modules.identity import IdentityService
from newray.modules.models import DEFAULT_MODEL_NAME, RUNTIME_OLLAMA, ModelInfo, ModelStatus
from newray.modules.profiles import DEFAULT_BINDING_NAME, ProfileService

MODEL = ModelInfo(
    name=DEFAULT_MODEL_NAME,
    runtime=RUNTIME_OLLAMA,
    digest="sha256:abc123",
    status=ModelStatus.QUALIFIED,
    capabilities=("chat", "tools"),
)

OTHER_MODEL = ModelInfo(
    name="newray-gemma4-31b-it:ud-q4-k-xl",
    runtime=RUNTIME_OLLAMA,
    digest="sha256:def456",
    status=ModelStatus.INSTALLED,
    capabilities=("chat",),
)


def _profiles(catalog: InMemoryModelCatalog) -> ProfileService:
    profiles_repo = InMemoryProfileRepository()
    bindings_store = InMemoryModelBindingStore()
    return ProfileService(
        profiles_repo,
        bindings_store,
        catalog,
        FakeClock(datetime(2026, 9, 16, tzinfo=UTC)),
        InMemoryProfileDefaultsSeeder(profiles_repo, bindings_store),
        default_model_name="llama3.1",
        version_writer=InMemoryProfileVersionWriter(profiles_repo, bindings_store),
    )


@pytest.fixture()
def service() -> IdentityService:
    users = InMemoryUserRepository()
    sessions = InMemorySessionStore()
    return IdentityService(
        users,
        sessions,
        FakeClock(datetime(2026, 9, 16, tzinfo=UTC)),
        InMemoryOwnerBootstrap(users, sessions),
    )


@pytest.fixture()
def conversations() -> ConversationService:
    return ConversationService(
        InMemoryConversationRepository(),
        InMemoryMessageStore(),
        FakeClock(datetime(2026, 9, 16, tzinfo=UTC)),
    )


@pytest.fixture()
def client(service: IdentityService, conversations: ConversationService) -> TestClient:
    """App con catalogo vuoto: il modello di default non è disponibile."""
    return TestClient(
        create_app(service, conversations, _profiles(InMemoryModelCatalog()), cookie_secure=False),
        headers={"Origin": "http://testserver"},
    )


@pytest.fixture()
def client_with_model(service: IdentityService, conversations: ConversationService) -> TestClient:
    """App con il modello di default presente nel catalogo."""
    return TestClient(
        create_app(
            service, conversations, _profiles(InMemoryModelCatalog((MODEL,))), cookie_secure=False
        ),
        headers={"Origin": "http://testserver"},
    )


@pytest.fixture()
def client_with_models(service: IdentityService, conversations: ConversationService) -> TestClient:
    """App con due modelli nel catalogo: il default e uno alternativo per lo switch."""
    return TestClient(
        create_app(
            service,
            conversations,
            _profiles(InMemoryModelCatalog((MODEL, OTHER_MODEL))),
            cookie_secure=False,
        ),
        headers={"Origin": "http://testserver"},
    )


def _bootstrap(client: TestClient) -> None:
    response = client.post(
        "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
    )
    assert response.status_code == 201
    provisioned = client.post("/api/v1/profiles/defaults")
    assert provisioned.status_code == 200
    assert provisioned.json()["kind"] == "assistant"


def _profile_items(client: TestClient) -> list[dict]:
    response = client.get("/api/v1/profiles")
    assert response.status_code == 200
    return response.json()["items"]


def test_endpoint_chiedono_autenticazione(client: TestClient) -> None:
    for path in (
        "/api/v1/profiles",
        "/api/v1/models",
        "/api/v1/models/readiness",
        f"/api/v1/profiles/{uuid.uuid4()}/binding",
    ):
        response = client.get(path)
        assert response.status_code == 401
    body = client.get("/api/v1/profiles").json()
    assert set(body) == {"code", "message", "retryable", "correlation_id"}
    assert body["code"] == "UNAUTHENTICATED"
    switch_response = client.post(
        f"/api/v1/profiles/{uuid.uuid4()}/versions", json={"model_name": "x"}
    )
    assert switch_response.status_code == 401
    assert client.post("/api/v1/profiles/defaults").status_code == 401


def test_readiness_autenticata_non_carica_e_non_qualifica(client: TestClient) -> None:
    response = client.post(
        "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
    )
    assert response.status_code == 201
    readiness = client.get("/api/v1/models/readiness")
    assert readiness.status_code == 200
    assert readiness.json() == {
        "state": "catalog_empty",
        "model_name": "llama3.1",
        "digest": None,
        "declared_capabilities": [],
    }
    assert _profile_items(client) == []


def test_get_profili_non_provisiona(client: TestClient) -> None:
    response = client.post(
        "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
    )
    assert response.status_code == 201
    assert _profile_items(client) == []
    first = client.post("/api/v1/profiles/defaults")
    second = client.post("/api/v1/profiles/defaults")
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert [item["id"] for item in _profile_items(client)] == [first.json()["id"]]


def test_lista_profili_semanti_con_modello_mancente(client: TestClient) -> None:
    _bootstrap(client)
    items = _profile_items(client)
    assert len(items) == 1
    assert {item["kind"] for item in items} == {"assistant"}
    for item in items:
        assert set(item) == {
            "id",
            "kind",
            "display_name",
            "version",
            "created_at",
            "updated_at",
            "binding",
            "model",
        }
        assert item["version"] == "1.0.0"
        assert item["binding"] == {
            "name": DEFAULT_BINDING_NAME,
            "runtime": RUNTIME_OLLAMA,
            "model_name": DEFAULT_MODEL_NAME,
            "parameters": {},
        }
        # Modello non nel catalogo: disponibilità esplicitamente null.
        assert item["model"] is None


def test_lista_profili_con_modello_disponibile(client_with_model: TestClient) -> None:
    _bootstrap(client_with_model)
    items = _profile_items(client_with_model)
    assert len(items) == 1
    for item in items:
        assert item["model"] == {
            "name": DEFAULT_MODEL_NAME,
            "runtime": RUNTIME_OLLAMA,
            "digest": "sha256:abc123",
            "status": "qualified",
            "capabilities": ["chat", "tools"],
        }


def test_lista_modelli(client_with_model: TestClient) -> None:
    _bootstrap(client_with_model)
    response = client_with_model.get("/api/v1/models")
    assert response.status_code == 200
    payload = response.json()
    assert [model["name"] for model in payload["items"]] == [DEFAULT_MODEL_NAME]
    assert set(payload["items"][0]) == {"name", "runtime", "digest", "status", "capabilities"}


def test_lista_modelli_vuota(client: TestClient) -> None:
    _bootstrap(client)
    assert client.get("/api/v1/models").json() == {"items": []}


def test_risoluzione_binding_risponde_snapshot(client_with_model: TestClient) -> None:
    _bootstrap(client_with_model)
    profile_id = _profile_items(client_with_model)[0]["id"]
    response = client_with_model.get(f"/api/v1/profiles/{profile_id}/binding")
    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "profile_id": profile_id,
        "profile_version_id": payload["profile_version_id"],
        "binding_id": payload["binding_id"],
        "profile_version": "1.0.0",
        "binding_name": DEFAULT_BINDING_NAME,
        "runtime": RUNTIME_OLLAMA,
        "model_name": DEFAULT_MODEL_NAME,
        "digest": "sha256:abc123",
        "parameters": {},
        "capabilities": ["chat", "tools"],
        "instructions": payload["instructions"],
    }
    assert isinstance(payload["instructions"], str)
    assert len(payload["instructions"]) > 0
    assert uuid.UUID(payload["profile_version_id"])
    assert uuid.UUID(payload["binding_id"])
    # Stesso profilo, stesso stato del catalogo: snapshot identico.
    assert client_with_model.get(f"/api/v1/profiles/{profile_id}/binding").json() == payload


def test_risoluzione_modello_mancente_503_model_unavailable(client: TestClient) -> None:
    _bootstrap(client)
    profile_id = _profile_items(client)[0]["id"]
    response = client.get(f"/api/v1/profiles/{profile_id}/binding")
    assert response.status_code == 503
    payload = response.json()
    assert set(payload) == {"code", "message", "retryable", "correlation_id"}
    assert payload["code"] == "MODEL_UNAVAILABLE"
    # Recuperabile per un'azione esplicita, non per un retry cieco.
    assert payload["retryable"] is False


def test_risoluzione_profilo_sconosciuto_404(client_with_model: TestClient) -> None:
    _bootstrap(client_with_model)
    response = client_with_model.get(f"/api/v1/profiles/{uuid.uuid4()}/binding")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_risoluzione_id_non_uuid_422(client_with_model: TestClient) -> None:
    _bootstrap(client_with_model)
    response = client_with_model.get("/api/v1/profiles/non-uuid/binding")
    assert response.status_code == 422


def test_switch_model_crea_una_versione_nuova(
    service: IdentityService, conversations: ConversationService
) -> None:
    clock = FakeClock(datetime(2026, 9, 16, tzinfo=UTC))
    profiles_repo = InMemoryProfileRepository()
    bindings_store = InMemoryModelBindingStore()
    profiles = ProfileService(
        profiles_repo,
        bindings_store,
        InMemoryModelCatalog((MODEL, OTHER_MODEL)),
        clock,
        InMemoryProfileDefaultsSeeder(profiles_repo, bindings_store),
        default_model_name="llama3.1",
        version_writer=InMemoryProfileVersionWriter(profiles_repo, bindings_store),
    )
    client = TestClient(
        create_app(service, conversations, profiles, cookie_secure=False),
        headers={"Origin": "http://testserver"},
    )
    _bootstrap(client)
    profile = _profile_items(client)[0]
    # Il clock avanza: la nuova versione ha un created_at proprio, altrimenti
    # il tiebreak sull'id è arbitrario (stesso contratto dell'adapter).
    clock.advance(timedelta(seconds=1))
    response = client.post(
        f"/api/v1/profiles/{profile['id']}/versions",
        json={
            "model_name": OTHER_MODEL.name,
            "expected_profile_version": profile["version"],
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["id"] == profile["id"]
    assert payload["version"] != profile["version"]
    assert payload["binding"]["model_name"] == OTHER_MODEL.name
    assert payload["model"]["name"] == OTHER_MODEL.name
    # La lista concorda: la nuova versione è quella corrente.
    reloaded = next(p for p in _profile_items(client) if p["id"] == profile["id"])
    assert reloaded["version"] == payload["version"]
    assert reloaded["binding"]["model_name"] == OTHER_MODEL.name


def test_switch_model_versione_attesa_obsoleta_409(client_with_models: TestClient) -> None:
    _bootstrap(client_with_models)
    profile = _profile_items(client_with_models)[0]
    response = client_with_models.post(
        f"/api/v1/profiles/{profile['id']}/versions",
        json={"model_name": OTHER_MODEL.name, "expected_profile_version": "9.9.9"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


def test_switch_model_idempotenza_stessa_chiave_stesso_esito(
    client_with_models: TestClient,
) -> None:
    _bootstrap(client_with_models)
    profile = _profile_items(client_with_models)[0]
    body = {
        "model_name": OTHER_MODEL.name,
        "expected_profile_version": profile["version"],
        "idempotency_key": "retry-http-1",
    }
    first = client_with_models.post(f"/api/v1/profiles/{profile['id']}/versions", json=body)
    second = client_with_models.post(f"/api/v1/profiles/{profile['id']}/versions", json=body)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["version"] == second.json()["version"]


def test_switch_model_chiave_riusata_con_payload_diverso_409(
    client_with_models: TestClient,
) -> None:
    _bootstrap(client_with_models)
    profile = _profile_items(client_with_models)[0]
    client_with_models.post(
        f"/api/v1/profiles/{profile['id']}/versions",
        json={
            "model_name": OTHER_MODEL.name,
            "expected_profile_version": profile["version"],
            "idempotency_key": "retry-http-2",
        },
    )
    response = client_with_models.post(
        f"/api/v1/profiles/{profile['id']}/versions",
        json={"model_name": MODEL.name, "idempotency_key": "retry-http-2"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


def test_switch_model_modello_assente_503(client_with_models: TestClient) -> None:
    _bootstrap(client_with_models)
    profile = _profile_items(client_with_models)[0]
    response = client_with_models.post(
        f"/api/v1/profiles/{profile['id']}/versions",
        json={"model_name": "modello-inesistente"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "MODEL_UNAVAILABLE"


def test_switch_model_profilo_sconosciuto_404(client_with_models: TestClient) -> None:
    _bootstrap(client_with_models)
    response = client_with_models.post(
        f"/api/v1/profiles/{uuid.uuid4()}/versions", json={"model_name": OTHER_MODEL.name}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_switch_model_nome_vuoto_422(client_with_models: TestClient) -> None:
    _bootstrap(client_with_models)
    profile = _profile_items(client_with_models)[0]
    response = client_with_models.post(
        f"/api/v1/profiles/{profile['id']}/versions", json={"model_name": "   "}
    )
    assert response.status_code == 422


def test_repository_sospeso_non_blocca_una_richiesta_indipendente(
    service: IdentityService, conversations: ConversationService, monkeypatch
) -> None:
    principal = service.bootstrap_owner("Ada", "test-passphrase-1234")
    profiles = _profiles(InMemoryModelCatalog((MODEL,)))
    profiles.ensure_defaults(principal)
    view = asyncio.run(profiles.list_profiles(principal))[0]
    original = profiles._profiles.get_profile
    entered, release = threading.Event(), threading.Event()

    def suspended(scope, profile_id):
        entered.set()
        assert release.wait(3), "il ciclo eventi è rimasto bloccato sul repository"
        return original(scope, profile_id)

    monkeypatch.setattr(profiles._profiles, "get_profile", suspended)
    app = create_app(service, conversations, profiles)
    app.dependency_overrides[require_principal] = lambda: principal

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            pending = asyncio.create_task(client.get(f"/api/v1/profiles/{view.profile.id}/binding"))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                independent = await asyncio.wait_for(client.get("/api/v1/models"), 1)
                assert independent.status_code == 200
                assert not pending.done()
            finally:
                release.set()
            assert (await pending).status_code == 200

    asyncio.run(scenario())
