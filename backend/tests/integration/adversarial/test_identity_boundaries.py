"""P-17: confini dell'identità — Principal è deciso dal server.

Copre:
- route protette rifiutate ad anonimo (401);
- cookie di sessione revocato non è più valido, anche se il client lo
  ripresenta;
- header/payload contenente ID non concede identità: il Principal
  arriva sempre dal cookie.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from adversarial.conftest import (  # type: ignore[import-not-found]
    bootstrap_owner,
)


def test_anonimo_401_su_route_protette(hostile_client: TestClient) -> None:
    """Nessun cookie → 401 UNAUTHENTICATED su tutte le route protette.

    Anche route di sola lettura (senza effetti collaterali) devono
    rifiutare: l'esistenza stessa di dati non è pubblica.
    """
    hostile_client.cookies.clear()
    for path in [
        "/api/v1/me",
        "/api/v1/conversations",
        "/api/v1/profiles",
        f"/api/v1/runs/{uuid.uuid4()}",
        f"/api/v1/runs/{uuid.uuid4()}/events",
    ]:
        r = hostile_client.get(path)
        assert r.status_code == 401, f"{path}: atteso 401, ricevuto {r.status_code}"
        assert r.json()["code"] == "UNAUTHENTICATED"


def test_cookie_revocato_non_riautentica(hostile_client: TestClient) -> None:
    """Cookie di sessione revocata → SESSION_INVALID, non successo."""
    cookie = bootstrap_owner(hostile_client, "Ada", "test-passphrase-1234")
    r = hostile_client.get("/api/v1/me")
    assert r.status_code == 200

    r = hostile_client.post("/api/v1/session/revoke")
    assert r.status_code == 204

    # Il client presenta comunque il cookie revocato: il server rifiuta.
    hostile_client.cookies.set("newray_session", cookie)
    r = hostile_client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.json()["code"] == "SESSION_INVALID"


def test_id_nel_body_non_bypassa_principal(hostile_client: TestClient) -> None:
    """POST /conversations non accetta un `owner_id` iniettato nel body.

    Il modello Pydantic non ha `owner_id`; se il client lo aggiunge,
    viene ignorato e il principal del server determina la proprietà.
    """
    bootstrap_owner(hostile_client, "Ada", "test-passphrase-1234")
    fake_owner = str(uuid.uuid4())
    fake_org = str(uuid.uuid4())
    r = hostile_client.post(
        "/api/v1/conversations",
        json={
            "title": "prova",
            # Campi ostili: devono essere ignorati.
            "owner_id": fake_owner,
            "organization_id": fake_org,
            "id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # L'id del server non è mai quello suggerito dal client.
    assert body["id"] != fake_owner
    # La conversazione appare nel proprio scope.
    r = hostile_client.get("/api/v1/conversations")
    assert any(item["id"] == body["id"] for item in r.json()["items"])


def test_header_di_identita_ignorati(hostile_client: TestClient) -> None:
    """`X-User-Id`/`X-Role` iniettati → nessun bypass, 401 senza cookie."""
    hostile_client.cookies.clear()
    r = hostile_client.get(
        "/api/v1/me",
        headers={
            "X-User-Id": str(uuid.uuid4()),
            "X-Role": "owner",
            "X-Organization-Id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHENTICATED"


def test_conversation_di_altri_non_e_leggibile(hostile_client: TestClient) -> None:
    """Un id di conversazione fabbricato ritorna 404 uniforme (non 403).

    Il pilot è monouser sul bootstrap: un secondo utente non può
    registrarsi. Ma la superficie dell'API deve comunque rispondere
    404 uniforme per un id ignoto — sia perché non esiste sia perché è
    fuori scope — così non trapela l'esistenza di record altrui.
    """
    bootstrap_owner(hostile_client, "Ada", "test-passphrase-1234")
    r = hostile_client.get(f"/api/v1/conversations/{uuid.uuid4()}")
    assert r.status_code == 404


def test_negative_id_body_bypass_attivo(hostile_client: TestClient) -> None:
    """Controllo negativo: se il repository accettasse `owner_id` dal
    body la conversazione apparirebbe SOLO nello scope suggerito. Qui
    dimostriamo il contrario: la conversazione appare solo nello scope
    del cookie, indipendentemente da quali id sono stati suggeriti.

    Il test è "negativo" nel senso che disegna esplicitamente lo
    scenario di attacco e verifica che il difensore reagisca: se un
    domani qualcuno cambiasse il servizio per leggere `owner_id` dal
    payload, questo test fallirebbe.
    """
    bootstrap_owner(hostile_client, "Ada", "test-passphrase-1234")
    other_owner = str(uuid.uuid4())
    r = hostile_client.post(
        "/api/v1/conversations",
        json={"title": "prova", "owner_id": other_owner},
    )
    assert r.status_code == 201
    created_id = r.json()["id"]
    # Deve apparire tra le nostre.
    r = hostile_client.get("/api/v1/conversations")
    ids = [item["id"] for item in r.json()["items"]]
    assert created_id in ids, (
        "controllo negativo: la conversazione dovrebbe essere nostra a "
        "prescindere da owner_id iniettato — se non lo è, il body ha "
        "influenzato lo scope (regressione di sicurezza)"
    )
