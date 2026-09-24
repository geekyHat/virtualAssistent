"""Percorso completo API → caso d'uso → dati su PostgreSQL reale (A-04).

Usa il ruolo applicativo ``newray_app`` del database di test fresco;
senza i DSN ``NEWRAY_TEST_*_URL`` il test si salta (suite offline).
"""

from __future__ import annotations

import pytest
from conftest import DatabaseHandles
from fastapi.testclient import TestClient

from newray.bootstrap.api import create_app
from newray.infrastructure.database import create_engine
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
from newray.modules.models.adapters.empty import EmptyModelCatalog
from newray.modules.profiles import ProfileService
from newray.modules.profiles.adapters.postgres import (
    PostgresModelBindingStore,
    PostgresProfileDefaultsSeeder,
    PostgresProfileRepository,
)


@pytest.fixture()
def client(test_databases: DatabaseHandles) -> TestClient:
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
    with TestClient(
        create_app(identity, conversations, profiles, cookie_secure=False),
        headers={"Origin": "http://testserver"},
    ) as test_client:
        yield test_client
    engine.dispose()


def test_percorso_completo_bootstrap_me_revoca(client: TestClient) -> None:
    # Bootstrap via API: crea i dati reali ed emette il cookie.
    response = client.post(
        "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
    )
    assert response.status_code == 201
    identity = response.json()
    assert identity["display_name"] == "Ada"
    assert identity["role"] == "owner"
    cookie_value = response.headers["set-cookie"].split(";")[0].split("=", 1)[1]

    # /me risolve il principal dal cookie, leggi su PostgreSQL con RLS.
    response = client.get("/api/v1/me")
    assert response.status_code == 200
    assert response.json() == identity

    # Il bootstrap è monouso: il secondo tentativo conflittua.
    response = client.post(
        "/api/v1/session", json={"display_name": "Eva", "credential": "test-passphrase-1234"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"

    # Revoca: 204; la sessione revocata non risolve più, anche se il
    # cookie scaduto continua a essere presentato dal client.
    assert client.post("/api/v1/session/revoke").status_code == 204
    client.cookies.set("newray_session", cookie_value)
    response = client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["code"] == "SESSION_INVALID"

    # Senza cookie la risposta è 401 UNAUTHENTICATED.
    client.cookies.clear()
    response = client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_normalizzazione_nome_bootstrap_e_login(client: TestClient) -> None:
    """B-03.2-30: il nome è normalizzato identicamente ai due confini.

    Bootstrap con margini di whitespace → il nome persistito è quello
    pulito; revoca → login con un nome "sporco" raggiunge lo stesso
    owner e il ``/me`` riporta il nome normalizzato.
    """
    response = client.post(
        "/api/v1/session",
        json={"display_name": "  Ada Lovelace  ", "credential": "test-passphrase-1234"},
    )
    assert response.status_code == 201
    assert response.json()["display_name"] == "Ada Lovelace"

    assert client.post("/api/v1/session/revoke").status_code == 204
    response = client.post(
        "/api/v1/session/login",
        json={"display_name": " Ada Lovelace", "credential": "test-passphrase-1234"},
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "Ada Lovelace"

    assert client.get("/api/v1/me").json()["display_name"] == "Ada Lovelace"


def test_nome_vuoto_dopo_normalizzazione_e_rifiutato(client: TestClient) -> None:
    """B-03.2-30: solo whitespace → 400 INVALID_NAME, mai un tentativo di
    credenziale né un bootstrap riuscito. Lo status pubblico conferma
    che l'installazione resta non bootstrappata.
    """
    response = client.post(
        "/api/v1/session", json={"display_name": "   ", "credential": "test-passphrase-1234"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_NAME"
    assert client.get("/api/v1/session/status").json()["bootstrapped"] is False

    response = client.post(
        "/api/v1/session/login", json={"display_name": "  ", "credential": "test-passphrase-1234"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_NAME"
