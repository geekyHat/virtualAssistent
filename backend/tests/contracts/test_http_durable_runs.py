"""Contratto HTTP dei run durevoli (P-05, NewRay.md §19.3).

Solo creazione idempotente e lettura dello snapshot: il worker che consuma
la coda non gira in questi test (``InMemoryRunStore`` resta ``queued``).
Cancellazione ed eventi/stream restano P-06.
"""

from __future__ import annotations

from datetime import UTC, datetime

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
    InMemoryRunStore,
    InMemorySessionStore,
    InMemoryUserRepository,
)
from newray.bootstrap.api import create_app
from newray.modules.conversations import ConversationService
from newray.modules.identity import IdentityService
from newray.modules.models import RUNTIME_OLLAMA, ModelInfo, ModelStatus
from newray.modules.models.adapters.echo import EchoChatModel
from newray.modules.profiles import ProfileService

MODEL = ModelInfo(
    name="llama3.1",
    runtime=RUNTIME_OLLAMA,
    digest="sha256:abc123",
    status=ModelStatus.QUALIFIED,
    capabilities=("chat",),
)


def _profiles() -> ProfileService:
    catalog = InMemoryModelCatalog((MODEL,))
    profiles_repo = InMemoryProfileRepository()
    bindings_store = InMemoryModelBindingStore()
    return ProfileService(
        profiles_repo,
        bindings_store,
        catalog,
        FakeClock(datetime(2026, 9, 24, tzinfo=UTC)),
        InMemoryProfileDefaultsSeeder(profiles_repo, bindings_store),
        default_model_name="llama3.1",
    )


@pytest.fixture()
def service() -> IdentityService:
    users = InMemoryUserRepository()
    sessions = InMemorySessionStore()
    return IdentityService(
        users,
        sessions,
        FakeClock(datetime(2026, 9, 24, tzinfo=UTC)),
        InMemoryOwnerBootstrap(users, sessions),
    )


@pytest.fixture()
def conversations() -> ConversationService:
    return ConversationService(
        InMemoryConversationRepository(),
        InMemoryMessageStore(),
        FakeClock(datetime(2026, 9, 24, tzinfo=UTC)),
    )


def _client(
    service: IdentityService,
    conversations: ConversationService,
    *,
    max_queue_depth: int = 50,
) -> TestClient:
    return TestClient(
        create_app(
            service,
            conversations,
            _profiles(),
            cookie_secure=False,
            chat_model=EchoChatModel(),
            run_store=InMemoryRunStore(),
            runs_max_queue_depth=max_queue_depth,
        ),
        headers={"Origin": "http://testserver"},
    )


def _bootstrap(client: TestClient) -> None:
    response = client.post(
        "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
    )
    assert response.status_code == 201


def _create_conversation(client: TestClient) -> str:
    response = client.post("/api/v1/conversations", json={"title": "Lavoro"})
    assert response.status_code == 201
    return response.json()["id"]


def _default_profile_id(client: TestClient) -> str:
    assert client.post("/api/v1/profiles/defaults").status_code == 200
    response = client.get("/api/v1/profiles")
    assert response.status_code == 200
    return response.json()["items"][0]["id"]


def test_creazione_senza_sessione_401(
    service: IdentityService, conversations: ConversationService
) -> None:
    client = _client(service, conversations)
    response = client.post(
        "/api/v1/conversations/00000000-0000-0000-0000-000000000000/runs",
        json={
            "profile_id": "00000000-0000-0000-0000-000000000000",
            "content": "Ciao",
            "idempotency_key": "k1",
        },
    )
    assert response.status_code == 401


def test_creazione_replay_conflitto_e_lettura(
    service: IdentityService, conversations: ConversationService
) -> None:
    client = _client(service, conversations)
    _bootstrap(client)
    conversation_id = _create_conversation(client)
    profile_id = _default_profile_id(client)

    created = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": profile_id, "content": "Ciao", "idempotency_key": "run-1"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["state"] == "queued"
    assert body["partial_text"] == ""
    assert body["finish_reason"] is None
    assert body["conversation_id"] == conversation_id

    replay = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": profile_id, "content": "Ciao", "idempotency_key": "run-1"},
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == body["id"]

    conflict = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": profile_id, "content": "Diverso", "idempotency_key": "run-1"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "CONFLICT"

    read = client.get(f"/api/v1/runs/{body['id']}")
    assert read.status_code == 200
    assert read.json() == body


def test_conversazione_inesistente_404(
    service: IdentityService, conversations: ConversationService
) -> None:
    client = _client(service, conversations)
    _bootstrap(client)
    profile_id = _default_profile_id(client)
    response = client.post(
        "/api/v1/conversations/00000000-0000-0000-0000-000000000000/runs",
        json={"profile_id": profile_id, "content": "Ciao", "idempotency_key": "k1"},
    )
    assert response.status_code == 404


def test_run_inesistente_404(service: IdentityService, conversations: ConversationService) -> None:
    """Lettura di un run mai creato: 404 uniforme (§7.3).

    L'isolamento fra due principal reali è provato allo stesso livello del
    ``RunStore`` (``test_durable_runs_application.py``) e su PostgreSQL
    reale con RLS (``tests/integration``): qui basta il contratto HTTP.
    """
    client = _client(service, conversations)
    _bootstrap(client)
    response = client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_coda_piena_503(service: IdentityService, conversations: ConversationService) -> None:
    client = _client(service, conversations, max_queue_depth=1)
    _bootstrap(client)
    conversation_id = _create_conversation(client)
    profile_id = _default_profile_id(client)

    first = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": profile_id, "content": "Ciao", "idempotency_key": "run-1"},
    )
    assert first.status_code == 201

    second = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": profile_id, "content": "Altro", "idempotency_key": "run-2"},
    )
    assert second.status_code == 503
    assert second.json()["code"] == "QUEUE_FULL"


def test_validazione_422(service: IdentityService, conversations: ConversationService) -> None:
    client = _client(service, conversations)
    _bootstrap(client)
    conversation_id = _create_conversation(client)
    profile_id = _default_profile_id(client)

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": profile_id, "content": "", "idempotency_key": "run-1"},
    )
    assert response.status_code == 422

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": profile_id, "content": "Ciao", "idempotency_key": ""},
    )
    assert response.status_code == 422
