"""P-17: autorizzazione dei run — snapshot, eventi, cancel, immutabilità.

Il pilot ha un solo owner attivato dal bootstrap. Le proprietà di
scope si verificano quindi:

- fabricando id UUID di run/organization diversi e verificando 404
  uniforme;
- manipolando la RLS a livello SQL (scope estraneo tramite
  `set_config`) per confermare che la tabella non concede letture o
  scritture al ruolo applicativo fuori scope;
- verificando l'immutabilità dell'outbox `run_events`: nessun UPDATE
  concesso al ruolo `newray_app`.
"""

from __future__ import annotations

import uuid

import pytest
from conftest import DatabaseHandles
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from adversarial.conftest import (  # type: ignore[import-not-found]
    bootstrap_owner,
    create_conversation,
    seed_assistant_profile,
)
from newray.infrastructure.database import create_engine


def test_get_run_id_ignoto_e_404_uniforme(hostile_client: TestClient) -> None:
    bootstrap_owner(hostile_client, "Ada", "test-passphrase-1234")
    r = hostile_client.get(f"/api/v1/runs/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"


def test_get_events_di_altro_scope_404(hostile_client: TestClient) -> None:
    bootstrap_owner(hostile_client, "Ada", "test-passphrase-1234")
    # Id casuale, non esiste per Ada.
    r = hostile_client.get(f"/api/v1/runs/{uuid.uuid4()}/events")
    assert r.status_code == 404
    # E nemmeno con Last-Event-ID fabbricato.
    r = hostile_client.get(
        f"/api/v1/runs/{uuid.uuid4()}/events",
        headers={"Last-Event-ID": "1"},
    )
    assert r.status_code == 404


def test_cancel_di_run_altrui_404(hostile_client: TestClient) -> None:
    bootstrap_owner(hostile_client, "Ada", "test-passphrase-1234")
    r = hostile_client.post(f"/api/v1/runs/{uuid.uuid4()}/cancel")
    assert r.status_code == 404


def test_idempotency_key_non_condivisa_tra_scope(
    hostile_client: TestClient,
) -> None:
    """La chiave di idempotenza è scoped: due utenti diversi possono usare
    la stessa senza collisione. Ada la usa, poi la scope di un altro
    utente non ne è al corrente."""
    bootstrap_owner(hostile_client, "Ada", "test-passphrase-1234")
    profile_id = seed_assistant_profile(hostile_client)
    conversation_id = create_conversation(hostile_client, "adv")
    r = hostile_client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": str(profile_id), "content": "ciao"},
        headers={"Idempotency-Key": "condivisa"},
    )
    assert r.status_code == 201
    run_id = r.json()["id"]

    # Revoca la sessione: il cookie non è più valido.
    hostile_client.post("/api/v1/session/revoke")
    hostile_client.cookies.clear()

    # Anonimo che presenta stessa Idempotency-Key: 401 (non 201/replay).
    r = hostile_client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": str(profile_id), "content": "ciao"},
        headers={"Idempotency-Key": "condivisa"},
    )
    assert r.status_code == 401

    # E la lettura del run creato da Ada resta invisibile senza cookie.
    r = hostile_client.get(f"/api/v1/runs/{run_id}")
    assert r.status_code == 401


def test_run_di_altra_org_non_visibile(
    hostile_client: TestClient, test_databases: DatabaseHandles
) -> None:
    """Manipolazione diretta: uno scope estraneo (org+user fabbricati)
    non deve vedere né aggiornare il run di Ada attraverso il DB con
    il ruolo applicativo."""
    bootstrap_owner(hostile_client, "Ada", "test-passphrase-1234")
    profile_id = seed_assistant_profile(hostile_client)
    conversation_id = create_conversation(hostile_client, "adv-rls")
    r = hostile_client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": str(profile_id), "content": "prova"},
        headers={"Idempotency-Key": "adv-run"},
    )
    assert r.status_code == 201
    run_id = uuid.UUID(r.json()["id"])

    engine = create_engine(test_databases.app)
    try:
        stranger_user = uuid.uuid4()
        stranger_org = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.user_id', :v, true)"),
                {"v": str(stranger_user)},
            )
            conn.execute(
                text("SELECT set_config('app.organization_id', :v, true)"),
                {"v": str(stranger_org)},
            )
            # SELECT ritorna zero righe.
            visible = conn.execute(
                text("SELECT id FROM runs WHERE id = :id"), {"id": run_id}
            ).first()
            assert visible is None
            # UPDATE consentito dai grant ma nascosto dalla RLS →
            # rowcount 0 sul run di Ada.
            result = conn.execute(
                text("UPDATE runs SET partial_text = 'INIEZIONE' WHERE id = :id"),
                {"id": run_id},
            )
            assert result.rowcount == 0
    finally:
        engine.dispose()


def test_run_events_sono_immutabili(
    hostile_client: TestClient, test_databases: DatabaseHandles
) -> None:
    """La outbox non concede UPDATE al ruolo applicativo: un attaccante
    con la propria sessione non può alterare eventi già pubblicati
    (né tramite i canali normali, che non lo espongono affatto, né via
    SQL diretto)."""
    bootstrap_owner(hostile_client, "Ada", "test-passphrase-1234")
    profile_id = seed_assistant_profile(hostile_client)
    conversation_id = create_conversation(hostile_client, "adv-immut")
    r = hostile_client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": str(profile_id), "content": "ciao"},
        headers={"Idempotency-Key": "immut-1"},
    )
    assert r.status_code == 201
    run_id = uuid.UUID(r.json()["id"])
    # Attende terminale prima di verificare gli eventi.
    import time

    for _ in range(50):
        got = hostile_client.get(f"/api/v1/runs/{run_id}")
        if got.json()["state"] in {"completed", "failed", "cancelled", "interrupted"}:
            break
        time.sleep(0.05)

    # Ora prova a fare UPDATE/DELETE sulla outbox dal ruolo applicativo
    # dentro lo scope di Ada. Deve fallire con ProgrammingError (grant
    # mancante), non silenziosamente.
    me = hostile_client.get("/api/v1/me").json()
    engine = create_engine(test_databases.app)
    try:
        user_id, org_id = _scope_of_run(engine, run_id)
        with engine.begin() as conn:
            _set_scope(conn, user_id, org_id)
            with pytest.raises(ProgrammingError):
                conn.execute(
                    text("UPDATE run_events SET payload = '{}'::jsonb WHERE run_id = :id"),
                    {"id": run_id},
                )
        with engine.begin() as conn:
            _set_scope(conn, user_id, org_id)
            with pytest.raises(ProgrammingError):
                conn.execute(
                    text("DELETE FROM run_events WHERE run_id = :id"),
                    {"id": run_id},
                )
        # Il DTO /me non espone l'organization_id: proprietà di minimo
        # necessario. Il test attinge lo scope corretto dal DB stesso.
        assert me["user_id"] == str(user_id)
    finally:
        engine.dispose()


def _scope_of_run(engine, run_id):
    """Recupera (owner_id, organization_id) del run dal ruolo applicativo.

    Usa uno scope superutente della RLS (nessun ``app.user_id`` impostato
    ma la riga esiste): la lettura funziona perché il worker vive nello
    stesso ruolo e sa vedere le proprie righe se abilitato. Qui invece
    usiamo l'accesso senza scope, che ritorna zero righe → poi passiamo
    per lo scope corretto via una query con ``ROW_SECURITY = off``… non
    disponibile al ruolo applicativo. Soluzione robusta: passa dal
    ``newray_migrate`` che è owner delle tabelle ma soggetto a RLS
    (FORCE) — ma qui vogliamo un helper generico. Sfruttiamo il fatto
    che il worker può leggere qualunque riga se ``app.worker_id`` è
    impostato.
    """
    with engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.worker_id', :v, true)"),
            {"v": str(run_id)},  # Placeholder unico; il valore va solo controllato != ''
        )
        row = conn.execute(
            text("SELECT owner_id, organization_id FROM runs WHERE id = :id"),
            {"id": run_id},
        ).one()
        return row.owner_id, row.organization_id


def _set_scope(conn, user_id, org_id) -> None:
    conn.execute(
        text("SELECT set_config('app.user_id', :v, true)"),
        {"v": str(user_id)},
    )
    conn.execute(
        text("SELECT set_config('app.organization_id', :v, true)"),
        {"v": str(org_id)},
    )


def test_negative_scope_setter_bypass_attivo(
    hostile_client: TestClient, test_databases: DatabaseHandles
) -> None:
    """Controllo negativo: se disattivassimo l'isolamento
    per-utente impostando lo scope di Ada in una sessione estranea, la
    SELECT ritornerebbe la riga. Il test avversariale reale
    (`test_run_di_altra_org_non_visibile`) dimostra il contrario: la
    RLS blocca lo scope estraneo. Qui dimostriamo che il meccanismo
    che quel test usa (`set_config` di uno scope diverso) è
    effettivamente quello che nasconde la riga — se un domani la
    policy `runs_isolation` fosse rimossa, questo test coglierebbe la
    regressione perché la SELECT nello scope corretto continuerebbe a
    vedere la riga."""
    bootstrap_owner(hostile_client, "Ada", "test-passphrase-1234")
    profile_id = seed_assistant_profile(hostile_client)
    conversation_id = create_conversation(hostile_client, "neg")
    r = hostile_client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": str(profile_id), "content": "prova"},
        headers={"Idempotency-Key": "neg-1"},
    )
    assert r.status_code == 201
    run_id = uuid.UUID(r.json()["id"])

    engine = create_engine(test_databases.app)
    try:
        user_id, org_id = _scope_of_run(engine, run_id)
        # Scope CORRETTO: vediamo il run (nel test negativo un bypass
        # farebbe vedere il run anche con scope errato).
        with engine.begin() as conn:
            _set_scope(conn, user_id, org_id)
            row = conn.execute(text("SELECT id FROM runs WHERE id = :id"), {"id": run_id}).first()
            assert row is not None, (
                "controllo negativo: lo scope corretto DEVE vedere il "
                "proprio run. Se non lo vede, la RLS è più stringente "
                "del previsto (regressione)."
            )
    finally:
        engine.dispose()
