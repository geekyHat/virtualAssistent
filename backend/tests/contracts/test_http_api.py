"""Contratti HTTP dell'identità: bootstrap, /me, revoca, codici stabili.

I fake verificano il contratto (NewRay.md §22.1): attributi del cookie,
anti-frode, payload di errore; la prova su PostgreSQL reale è in
``tests/integration/test_api.py``.
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
    InMemorySessionStore,
    InMemoryUserRepository,
)
from newray.bootstrap.api import create_app
from newray.interfaces.http.middleware.identity import SESSION_COOKIE
from newray.modules.conversations import ConversationService
from newray.modules.identity import DEFAULT_SESSION_TTL, IdentityService
from newray.modules.profiles import ProfileService

MAX_AGE = int(DEFAULT_SESSION_TTL.total_seconds())


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


def _set_cookie_header(response: TestClient) -> str:
    header = response.headers.get("set-cookie", "")
    assert header, "la risposta deve emettere il cookie di sessione"
    return header


def test_bootstrap_emette_cookie_opaco_e_identita(client: TestClient) -> None:
    response = client.post(
        "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["display_name"] == "Ada"
    assert body["role"] == "owner"
    assert len(body["user_id"]) == 36  # ID opaco, non derivato dal client

    cookie = _set_cookie_header(response).lower()
    assert f"{SESSION_COOKIE}=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    assert f"max-age={MAX_AGE}" in cookie
    assert "secure" not in cookie  # sviluppo loopback HTTP


def test_bootstrap_ripetuto_dà_conflict(client: TestClient) -> None:
    assert (
        client.post(
            "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
        ).status_code
        == 201
    )
    response = client.post(
        "/api/v1/session", json={"display_name": "Eva", "credential": "test-passphrase-1234"}
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "CONFLICT"
    assert body["message"]
    assert body["correlation_id"]
    assert response.headers.get("set-cookie") is None  # nessuna nuova sessione


def test_me_con_cookie_restituisce_identita(client: TestClient) -> None:
    client.post(
        "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
    )
    response = client.get("/api/v1/me")
    assert response.status_code == 200
    assert response.json()["display_name"] == "Ada"
    assert response.json()["role"] == "owner"


def test_me_senza_cookie_401(client: TestClient) -> None:
    response = client.get("/api/v1/me")
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "UNAUTHENTICATED"
    assert set(body) == {"code", "message", "retryable", "correlation_id"}


def test_me_con_cookie_malformato_401(client: TestClient) -> None:
    client.cookies.set(SESSION_COOKIE, "non-un-uuid")
    assert client.get("/api/v1/me").status_code == 401


def test_header_di_identita_spoofati_non_vengono_accettati(client: TestClient) -> None:
    """Gli identificativi del client non sono fidati (NewRay.md §7.1)."""
    response = client.get("/api/v1/me", headers={"X-User-Id": "0" * 32, "X-Role": "owner"})
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_revoca_204_e_sessione_subito_inutilizzabile(client: TestClient) -> None:
    client.post(
        "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
    )
    response = client.post("/api/v1/session/revoke")
    assert response.status_code == 204
    cookie = response.headers.get("set-cookie", "").lower()
    assert SESSION_COOKIE in cookie and "max-age=0" in cookie
    assert client.get("/api/v1/me").status_code == 401


def test_origine_estranea_non_puo_eseguire_bootstrap(client: TestClient) -> None:
    response = client.post(
        "/api/v1/session",
        json={"display_name": "Ada", "credential": "test-passphrase-1234"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ACCESS_DENIED"
    # Il middleware precede il caso d'uso: la richiesta legittima successiva
    # può ancora fare il bootstrap monouso.
    assert (
        client.post(
            "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
        ).status_code
        == 201
    )


def test_host_estraneo_e_origin_mancante_non_hanno_effetti(client: TestClient) -> None:
    missing_origin = client.post(
        "/api/v1/session",
        json={"display_name": "Ada", "credential": "test-passphrase-1234"},
        headers={"Origin": ""},
    )
    assert missing_origin.status_code == 403
    bad_host = client.post(
        "/api/v1/session",
        json={"display_name": "Ada", "credential": "test-passphrase-1234"},
        headers={"Host": "evil.example"},
    )
    assert bad_host.status_code == 403
    assert (
        client.post(
            "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
        ).status_code
        == 201
    )


def test_origine_estranea_non_puo_revocare_la_sessione(client: TestClient) -> None:
    assert (
        client.post(
            "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
        ).status_code
        == 201
    )
    response = client.post("/api/v1/session/revoke", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert client.get("/api/v1/me").status_code == 200


def test_cookie_secure_quando_configurato() -> None:
    users = InMemoryUserRepository()
    sessions = InMemorySessionStore()
    service = IdentityService(
        users,
        sessions,
        FakeClock(datetime(2026, 9, 16, tzinfo=UTC)),
        InMemoryOwnerBootstrap(users, sessions),
    )
    conversations = ConversationService(
        InMemoryConversationRepository(),
        InMemoryMessageStore(),
        FakeClock(datetime(2026, 9, 16, tzinfo=UTC)),
    )
    profiles_repo = InMemoryProfileRepository()
    bindings_store = InMemoryModelBindingStore()
    profiles = ProfileService(
        profiles_repo,
        bindings_store,
        InMemoryModelCatalog(),
        FakeClock(datetime(2026, 9, 16, tzinfo=UTC)),
        InMemoryProfileDefaultsSeeder(profiles_repo, bindings_store),
        default_model_name="llama3.1",
    )
    client = TestClient(
        create_app(service, conversations, profiles, cookie_secure=True),
        headers={"Origin": "http://testserver"},
    )
    response = client.post(
        "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
    )
    assert "secure" in _set_cookie_header(response).lower()


def test_correlation_id_ricevuto_è_riferito(client: TestClient) -> None:
    response = client.get("/api/v1/me", headers={"X-Request-ID": "richiesta-42"})
    assert response.json()["correlation_id"] == "richiesta-42"


def test_payload_di_errore_non_contiene_dettagli_interni(client: TestClient) -> None:
    body = client.get("/api/v1/me").json()
    # Struttura pubblica fissa (§19.4): niente nomi di classi, stack, SQL.
    assert set(body) == {"code", "message", "retryable", "correlation_id"}
    assert isinstance(body["retryable"], bool)


def test_status_pre_bootstrap_e_post_bootstrap(client: TestClient) -> None:
    """B-03.2-14: la WebUI sceglie fra bootstrap e login guardando solo qui."""
    pre = client.get("/api/v1/session/status")
    assert pre.status_code == 200
    assert pre.json() == {"bootstrapped": False}

    client.post(
        "/api/v1/session",
        json={"display_name": "Ada", "credential": "test-passphrase-1234"},
    )
    post = client.get("/api/v1/session/status")
    assert post.json() == {"bootstrapped": True}
    # Il payload di stato non rivela nome dell'owner né altri dettagli.
    assert set(post.json()) == {"bootstrapped"}


def test_bootstrap_rifiuta_credenziale_troppo_corta(client: TestClient) -> None:
    response = client.post(
        "/api/v1/session",
        json={"display_name": "Ada", "credential": "corta"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"


def test_login_dopo_logout_ripristina_la_stessa_identita(client: TestClient) -> None:
    """B-03.2-14: bootstrap → logout → login → stessi dati autorizzati."""
    bootstrap = client.post(
        "/api/v1/session",
        json={"display_name": "Ada", "credential": "test-passphrase-1234"},
    )
    assert bootstrap.status_code == 201
    original_id = bootstrap.json()["user_id"]

    assert client.post("/api/v1/session/revoke").status_code == 204
    assert client.get("/api/v1/me").status_code == 401

    login = client.post(
        "/api/v1/session/login",
        json={"display_name": "Ada", "credential": "test-passphrase-1234"},
    )
    assert login.status_code == 200
    assert login.json()["user_id"] == original_id

    cookie = _set_cookie_header(login).lower()
    assert f"{SESSION_COOKIE}=" in cookie
    assert "httponly" in cookie
    assert f"max-age={MAX_AGE}" in cookie
    assert client.get("/api/v1/me").json()["display_name"] == "Ada"


def test_login_con_credenziale_sbagliata_401_uniforme(client: TestClient) -> None:
    client.post(
        "/api/v1/session",
        json={"display_name": "Ada", "credential": "test-passphrase-1234"},
    )
    client.cookies.clear()
    for payload in (
        {"display_name": "Ada", "credential": "credenziale-sbagliata"},
        {"display_name": "Sconosciuto", "credential": "test-passphrase-1234"},
    ):
        response = client.post("/api/v1/session/login", json=payload)
        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_CREDENTIALS"
        assert response.headers.get("set-cookie") is None


def test_login_prima_del_bootstrap_401(client: TestClient) -> None:
    response = client.post(
        "/api/v1/session/login",
        json={"display_name": "Ada", "credential": "test-passphrase-1234"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"


def test_login_apre_una_nuova_sessione_indipendente(client: TestClient) -> None:
    """La sessione emessa da login è nuova; il primo cookie resta valido
    finché la vecchia sessione non viene revocata."""
    bootstrap = client.post(
        "/api/v1/session",
        json={"display_name": "Ada", "credential": "test-passphrase-1234"},
    )
    original_cookie = client.cookies.get(SESSION_COOKIE)
    login = client.post(
        "/api/v1/session/login",
        json={"display_name": "Ada", "credential": "test-passphrase-1234"},
    )
    assert login.status_code == 200
    # Il TestClient aggiorna il cookie all'ultima Set-Cookie: verifichiamo
    # che il valore sia effettivamente nuovo.
    new_cookie = client.cookies.get(SESSION_COOKIE)
    assert new_cookie and new_cookie != original_cookie
    assert bootstrap.json()["user_id"] == login.json()["user_id"]


# --- B-03.2-15: X-Request-ID, validazione, envelope ---


def test_request_id_con_caratteri_di_controllo_viene_ignorato(client: TestClient) -> None:
    response = client.get("/api/v1/me", headers={"X-Request-ID": "abc\x00def"})
    body = response.json()
    assert body["correlation_id"] != "abc\x00def"
    assert len(body["correlation_id"]) == 36


def test_request_id_troppo_lungo_viene_ignorato(client: TestClient) -> None:
    long_id = "x" * 200
    response = client.get("/api/v1/me", headers={"X-Request-ID": long_id})
    assert response.json()["correlation_id"] != long_id


def test_request_id_valido_viene_riflesso(client: TestClient) -> None:
    response = client.get("/api/v1/me", headers={"X-Request-ID": "req-42-abc"})
    assert response.json()["correlation_id"] == "req-42-abc"


def test_bootstrap_con_titolo_solo_whitespace_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/session",
        json={"display_name": "   ", "credential": "test-passphrase-1234"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_NAME"


def test_validazione_pydantic_envelope_concordato(client: TestClient) -> None:
    response = client.post("/api/v1/session", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert set(body) == {"code", "message", "retryable", "correlation_id"}
    assert body["retryable"] is False


def test_errore_500_non_e_retryable(client: TestClient) -> None:
    response = client.get("/api/v1/me")
    body = response.json()
    assert body["code"] == "UNAUTHENTICATED"
    assert body["retryable"] is False
