"""Contratti HTTP di conversazioni e messaggi (NewRay.md §19.2; B-01).

I fake verificano il contratto (NewRay.md §22.1): stati, forma dei payload,
codici di errore stabili, validazione ai confini. La prova su PostgreSQL
reale è in ``tests/integration``.
"""

from __future__ import annotations

import uuid
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
    InMemorySessionStore,
    InMemoryUserRepository,
)
from newray.bootstrap.api import create_app
from newray.modules.conversations import ConversationService
from newray.modules.identity import IdentityService
from newray.modules.profiles import ProfileService


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
def profiles() -> ProfileService:
    profiles_repo = InMemoryProfileRepository()
    bindings_store = InMemoryModelBindingStore()
    return ProfileService(
        profiles_repo,
        bindings_store,
        InMemoryModelCatalog(),
        FakeClock(datetime(2026, 9, 16, tzinfo=UTC)),
        InMemoryProfileDefaultsSeeder(profiles_repo, bindings_store),
        default_model_name="llama3.1",
    )


@pytest.fixture()
def client(
    service: IdentityService, conversations: ConversationService, profiles: ProfileService
) -> TestClient:
    return TestClient(
        create_app(service, conversations, profiles, cookie_secure=False),
        headers={"Origin": "http://testserver"},
    )


def _bootstrap(client: TestClient) -> None:
    response = client.post(
        "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
    )
    assert response.status_code == 201


def _create_conversation(client: TestClient, title: str = "Lavoro") -> dict:
    response = client.post("/api/v1/conversations", json={"title": title})
    assert response.status_code == 201
    return response.json()


def test_conversazioni_richiedono_autenticazione(client: TestClient) -> None:
    id_sconosciuto = str(uuid.uuid4())
    endpoints = [
        ("get", "/api/v1/conversations", None),
        ("post", "/api/v1/conversations", {"title": "Lavoro"}),
        ("get", f"/api/v1/conversations/{id_sconosciuto}", None),
        ("patch", f"/api/v1/conversations/{id_sconosciuto}", {"title": "Altro"}),
        ("delete", f"/api/v1/conversations/{id_sconosciuto}", None),
        ("get", f"/api/v1/conversations/{id_sconosciuto}/messages", None),
        ("post", f"/api/v1/conversations/{id_sconosciuto}/messages", {"content": "ciao"}),
    ]
    for method, path, body in endpoints:
        response = client.request(method, path, json=body)
        assert response.status_code == 401, f"{method} {path} deve richiedere la sessione"


def test_crea_conversazione_ritorna_dto(client: TestClient) -> None:
    _bootstrap(client)
    body = _create_conversation(client, "Primo lavoro")
    assert set(body) == {"id", "title", "created_at", "updated_at"}
    assert body["title"] == "Primo lavoro"
    assert len(body["id"]) == 36  # ID opaco, non derivato dal client


def test_lista_vuota_senza_cursori(client: TestClient) -> None:
    _bootstrap(client)
    response = client.get("/api/v1/conversations")
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


def test_lista_pagina_con_cursore(client: TestClient) -> None:
    _bootstrap(client)
    _create_conversation(client, "Prima")
    _create_conversation(client, "Seconda")
    _create_conversation(client, "Terza")

    response = client.get("/api/v1/conversations", params={"limit": 2})
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None

    response = client.get(
        "/api/v1/conversations", params={"limit": 2, "cursor": body["next_cursor"]}
    )
    body2 = response.json()
    assert len(body2["items"]) == 1
    assert body2["next_cursor"] is None
    assert body2["items"][0]["id"] not in {item["id"] for item in body["items"]}


def test_cursore_non_valido_è_400(client: TestClient) -> None:
    _bootstrap(client)
    response = client.get("/api/v1/conversations", params={"cursor": "non-un-cursore"})
    assert response.status_code == 400
    assert response.json()["code"] == "DOMAIN_ERROR"


def test_lettura_404_per_id_sconosciuto(client: TestClient) -> None:
    _bootstrap(client)
    response = client.get(f"/api/v1/conversations/{uuid.uuid4()}")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert "correlation_id" in body


def test_rinomina_200_e_titolo_aggiornato(client: TestClient) -> None:
    _bootstrap(client)
    conversation = _create_conversation(client, "Vecchio")
    response = client.patch(f"/api/v1/conversations/{conversation['id']}", json={"title": "Nuovo"})
    assert response.status_code == 200
    assert response.json()["title"] == "Nuovo"
    assert response.json()["id"] == conversation["id"]


def test_cancellazione_204_poi_404(client: TestClient) -> None:
    _bootstrap(client)
    conversation = _create_conversation(client)
    response = client.delete(f"/api/v1/conversations/{conversation['id']}")
    assert response.status_code == 204
    assert client.get(f"/api/v1/conversations/{conversation['id']}").status_code == 404
    assert client.get(f"/api/v1/conversations/{conversation['id']}/messages").status_code == 404


def test_messaggio_creato_con_ruolo_e_sequenza_di_sistema(client: TestClient) -> None:
    _bootstrap(client)
    conversation = _create_conversation(client)
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages", json={"content": "ciao"}
    )
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "role", "content", "sequence", "created_at"}
    assert body["role"] == "user"
    assert body["sequence"] == 1
    assert body["content"] == "ciao"

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages", json={"content": "e poi?"}
    )
    assert response.json()["sequence"] == 2


def test_lista_messaggi_in_ordine_con_cursore(client: TestClient) -> None:
    _bootstrap(client)
    conversation = _create_conversation(client)
    for i in range(3):
        client.post(
            f"/api/v1/conversations/{conversation['id']}/messages", json={"content": f"m{i}"}
        )

    response = client.get(
        f"/api/v1/conversations/{conversation['id']}/messages", params={"limit": 2}
    )
    assert response.status_code == 200
    body = response.json()
    assert [m["sequence"] for m in body["items"]] == [1, 2]
    assert body["next_sequence"] == 2

    response = client.get(
        f"/api/v1/conversations/{conversation['id']}/messages",
        params={"after_sequence": body["next_sequence"], "limit": 2},
    )
    body2 = response.json()
    assert [m["sequence"] for m in body2["items"]] == [3]
    assert body2["next_sequence"] is None


def test_messaggio_in_conversazione_assente_è_404(client: TestClient) -> None:
    _bootstrap(client)
    response = client.post(
        f"/api/v1/conversations/{uuid.uuid4()}/messages", json={"content": "ciao"}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_contenuto_vuoto_è_422(client: TestClient) -> None:
    _bootstrap(client)
    conversation = _create_conversation(client)
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages", json={"content": ""}
    )
    assert response.status_code == 422


def test_titolo_troppo_lungo_è_422(client: TestClient) -> None:
    _bootstrap(client)
    response = client.post("/api/v1/conversations", json={"title": "x" * 201})
    assert response.status_code == 422


def test_limit_negativo_è_422(client: TestClient) -> None:
    _bootstrap(client)
    response = client.get("/api/v1/conversations", params={"limit": 0})
    assert response.status_code == 422


def test_titolo_solo_whitespace_è_422(client: TestClient) -> None:
    _bootstrap(client)
    response = client.post("/api/v1/conversations", json={"title": "   \t  "})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["retryable"] is False


def test_messaggio_solo_whitespace_è_422(client: TestClient) -> None:
    _bootstrap(client)
    conv = client.post("/api/v1/conversations", json={"title": "Test"}).json()
    response = client.post(f"/api/v1/conversations/{conv['id']}/messages", json={"content": "   "})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
