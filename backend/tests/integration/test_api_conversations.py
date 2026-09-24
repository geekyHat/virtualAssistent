"""Percorso completo API → caso d'uso → dati, conversazioni (B-01).

Usa il ruolo applicativo ``newray_app`` del database di test fresco;
senza i DSN ``NEWRAY_TEST_*_URL`` i test si saltano (suite offline).
Includono il secondo principal: la sua visibilità è nulla anche via API.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from conftest import DatabaseHandles
from fastapi.testclient import TestClient

from fakes import InMemoryModelCatalog
from newray.bootstrap.api import create_app
from newray.infrastructure.database import create_engine
from newray.kernel.clock import SystemClock
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
from newray.modules.models import (
    RUNTIME_OLLAMA,
    ChatRequest,
    Completion,
    ContentDelta,
    ModelInfo,
    ModelStatus,
    StreamEvent,
)
from newray.modules.models.adapters.echo import EchoChatModel
from newray.modules.profiles import ProfileService
from newray.modules.profiles.adapters.postgres import (
    PostgresModelBindingStore,
    PostgresProfileDefaultsSeeder,
    PostgresProfileRepository,
)

MODEL = ModelInfo(
    name="llama3.1",
    runtime=RUNTIME_OLLAMA,
    digest="sha256:integration",
    status=ModelStatus.QUALIFIED,
    capabilities=("chat",),
)


def _now() -> datetime:
    return datetime.now(UTC)


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
        InMemoryModelCatalog((MODEL,)),
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
        create_app(
            identity,
            conversations,
            profiles,
            cookie_secure=False,
            chat_model=EchoChatModel(),
        ),
        headers={"Origin": "http://testserver"},
    )
    test_client.cookies.set("newray_session", str(owner.session_id))
    return test_client


@pytest.fixture()
def client_b(app_setup) -> TestClient:
    identity, conversations, profiles, _, session_b = app_setup
    test_client = TestClient(
        create_app(
            identity,
            conversations,
            profiles,
            cookie_secure=False,
            chat_model=EchoChatModel(),
        ),
        headers={"Origin": "http://testserver"},
    )
    test_client.cookies.set("newray_session", str(session_b.id))
    return test_client


def test_percorso_completo_conversazioni(client: TestClient) -> None:
    # Creazione e lettura della conversazione.
    response = client.post("/api/v1/conversations", json={"title": "Progetto Alpha"})
    assert response.status_code == 201
    conversation = response.json()
    assert conversation["title"] == "Progetto Alpha"

    response = client.get("/api/v1/conversations")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == conversation["id"]

    # Messaggi con sequenza server-side.
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages", json={"content": "primo"}
    )
    assert response.status_code == 201
    assert response.json()["sequence"] == 1
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages", json={"content": "secondo"}
    )
    assert response.json()["sequence"] == 2

    response = client.get(f"/api/v1/conversations/{conversation['id']}/messages")
    assert response.status_code == 200
    body = response.json()
    assert [m["sequence"] for m in body["items"]] == [1, 2]
    assert [m["content"] for m in body["items"]] == ["primo", "secondo"]

    # Rinomina aggiorna la conversazione.
    response = client.patch(
        f"/api/v1/conversations/{conversation['id']}", json={"title": "Progetto Beta"}
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Progetto Beta"

    # Cancellazione fisica con i messaggi.
    response = client.delete(f"/api/v1/conversations/{conversation['id']}")
    assert response.status_code == 204
    assert client.get(f"/api/v1/conversations/{conversation['id']}").status_code == 404
    assert client.get("/api/v1/conversations").json()["items"] == []


def test_secondo_principal_non_vede_i_dati_altrui(client: TestClient, client_b: TestClient) -> None:
    conversation = client.post("/api/v1/conversations", json={"title": "Di A"}).json()

    # B, autenticato con la propria sessione, non vede né tocca i dati di A.
    assert client_b.get("/api/v1/conversations").json() == {"items": [], "next_cursor": None}
    assert client_b.get(f"/api/v1/conversations/{conversation['id']}").status_code == 404
    assert (
        client_b.post(
            f"/api/v1/conversations/{conversation['id']}/messages", json={"content": "invasione"}
        ).status_code
        == 404
    )
    assert client_b.delete(f"/api/v1/conversations/{conversation['id']}").status_code == 404

    # I dati di A restano intatti.
    assert client.get(f"/api/v1/conversations/{conversation['id']}").status_code == 200
    assert client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"] == []


def _sse(response) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    name: str | None = None
    for line in response.iter_lines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: ") and name is not None:
            events.append((name, json.loads(line[6:])))
            name = None
    return events


def test_preview_inline_atomica_idempotente_e_isolata(
    client: TestClient, client_b: TestClient
) -> None:
    conversation = client.post("/api/v1/conversations", json={"title": "Chat A"}).json()
    profile_a = client.post("/api/v1/profiles/defaults").json()["id"]
    profile_b = client_b.post("/api/v1/profiles/defaults").json()["id"]
    payload = {"content": "ciao", "profile_id": profile_a}
    headers = {"Idempotency-Key": "integration-request-1"}

    with client.stream(
        "POST",
        f"/api/v1/conversations/{conversation['id']}/run",
        json=payload,
        headers=headers,
    ) as response:
        assert response.status_code == 200
        first = _sse(response)
    with client.stream(
        "POST",
        f"/api/v1/conversations/{conversation['id']}/run",
        json=payload,
        headers=headers,
    ) as response:
        replay = _sse(response)

    assert first[-1][0] == replay[-1][0] == "done"
    assert first[-1][1]["replayed"] is False
    assert replay[-1][1]["replayed"] is True
    messages = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
    assert [message["role"] for message in messages] == ["user", "assistant"]

    denied = client_b.post(
        f"/api/v1/conversations/{conversation['id']}/run",
        json={"content": "intrusione", "profile_id": profile_b},
    )
    assert denied.status_code == 404
    unchanged = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
    assert unchanged == messages


class LengthModel:
    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        async def generate() -> AsyncIterator[StreamEvent]:
            yield ContentDelta("parziale")
            yield Completion("length", 8, 3, 1_500_000_000)

        return generate()


def test_preview_length_persistito_come_troncamento(app_setup) -> None:
    identity, conversations, profiles, owner, _ = app_setup
    client = TestClient(
        create_app(
            identity,
            conversations,
            profiles,
            cookie_secure=False,
            chat_model=LengthModel(),
        ),
        headers={"Origin": "http://testserver"},
    )
    client.cookies.set("newray_session", str(owner.session_id))
    conversation = client.post("/api/v1/conversations", json={"title": "Length"}).json()
    profile_id = client.post("/api/v1/profiles/defaults").json()["id"]
    with client.stream(
        "POST",
        f"/api/v1/conversations/{conversation['id']}/run",
        json={"content": "continua", "profile_id": profile_id},
    ) as response:
        events = _sse(response)
    assert events[-1][1]["finish_reason"] == "length"
    assert events[-1][1]["tokens_per_second"] == 2.0
