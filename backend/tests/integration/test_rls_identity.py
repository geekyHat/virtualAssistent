"""RLS con due principal su PostgreSQL reale (NewRay.md §§7.3, 22.2).

Verifica la seconda barriera con il ruolo applicativo reale
(``newray_app``, senza superuser/BYPASSRLS): due utenti della stessa
organizzazione, un'organizzazione esterna, revoca e proprietario delle
tabelle. Niente fake: questi test identificano schema e ruoli reali.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest
from conftest import DatabaseHandles
from sqlalchemy import create_engine, text

from newray.infrastructure.database import create_engine as create_db_engine
from newray.kernel.clock import SystemClock
from newray.kernel.errors import Conflict, CredentialNotSet, InvalidCredentials, SessionInvalid
from newray.kernel.identity import Principal, Role, Scope, new_id
from newray.modules.identity import IdentityService
from newray.modules.identity.adapters.postgres import (
    PostgresOwnerBootstrap,
    PostgresSessionStore,
    PostgresUserRepository,
)
from newray.modules.identity.domain import Organization, User


def _query(dsn: str, sql: str, params: dict | None = None) -> list[tuple]:
    with create_engine(dsn).connect() as conn:
        return [tuple(row) for row in conn.execute(text(sql), params or {})]


def _as_principal(dsn: str, scope: Scope, sql: str) -> list[tuple]:
    """Esegue la query nel contesto del principal (transazione con GUC)."""
    with create_engine(dsn).connect() as conn:
        with conn.begin():
            conn.execute(
                text("SELECT set_config('app.user_id', :v, true)"),
                {"v": str(scope.user_id)},
            )
            conn.execute(
                text("SELECT set_config('app.organization_id', :v, true)"),
                {"v": str(scope.organization_id)},
            )
            return [tuple(row) for row in conn.execute(text(sql))]


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture()
def identity(test_databases: DatabaseHandles):
    """Service collegato al database fresco con il ruolo applicativo."""
    engine = create_db_engine(test_databases.app)
    service = IdentityService(
        PostgresUserRepository(engine),
        PostgresSessionStore(engine),
        SystemClock(),
        PostgresOwnerBootstrap(engine),
    )
    yield test_databases, engine, service
    engine.dispose()


def test_due_principal_stessa_organizzazione_sono_isolati(identity) -> None:
    dbs, engine, service = identity
    owner = service.bootstrap_owner("A", "test-passphrase-1234")
    b = User(new_id(), owner.organization_id, "B", Role.MEMBER, _now())
    assert PostgresUserRepository(engine).add_user(b) is True
    service.create_session(b)

    scope_a = Scope(owner.organization_id, owner.user_id)
    scope_b = Scope(owner.organization_id, b.id)

    # A vede solo i propri dati, B vede solo i propri (NewRay.md §7.3).
    assert _as_principal(dbs.app, scope_a, "SELECT count(*) FROM users") == [(1,)]
    assert _as_principal(dbs.app, scope_b, "SELECT count(*) FROM users") == [(1,)]
    assert _as_principal(dbs.app, scope_a, "SELECT count(*) FROM sessions") == [(1,)]
    assert _as_principal(dbs.app, scope_b, "SELECT count(*) FROM sessions") == [(1,)]
    assert _as_principal(dbs.app, scope_a, "SELECT count(*) FROM organizations") == [(1,)]
    assert _as_principal(dbs.app, scope_b, "SELECT count(*) FROM organizations") == [(1,)]

    # I repository, pur con il ruolo applicativo, non attraversano lo scope.
    repo = PostgresUserRepository(engine)
    assert repo.get_user(scope_b, owner.user_id) is None
    assert repo.get_user(scope_a, b.id) is None


def test_senza_contesto_nessun_dato(identity) -> None:
    dbs, _, service = identity
    service.bootstrap_owner("A", "test-passphrase-1234")
    # GUC assenti → policy non soddisfatta: default deny (NewRay.md §7.3).
    assert _query(dbs.app, "SELECT count(*) FROM users") == [(0,)]
    assert _query(dbs.app, "SELECT count(*) FROM sessions") == [(0,)]
    assert _query(dbs.app, "SELECT count(*) FROM organizations") == [(0,)]


def test_proprietario_tabelle_non_elude_rls(identity) -> None:
    """FORCE ROW LEVEL SECURITY: vale anche per il ruolo delle migrazioni."""
    dbs, _, service = identity
    service.bootstrap_owner("A", "test-passphrase-1234")
    assert _query(dbs.migration, "SELECT count(*) FROM users") == [(0,)]
    assert _query(dbs.migration, "SELECT count(*) FROM sessions") == [(0,)]
    assert _query(dbs.migration, "SELECT count(*) FROM organizations") == [(0,)]


def test_organizzazione_esterna_non_legge(identity) -> None:
    dbs, engine, service = identity
    owner = service.bootstrap_owner("A", "test-passphrase-1234")
    org2 = Organization(new_id(), "Altro", _now())
    repo = PostgresUserRepository(engine)
    c = User(new_id(), org2.id, "C", Role.MEMBER, _now())
    repo.add_organization(Scope(org2.id, c.id), org2)
    assert repo.add_user(c) is True
    service.create_session(c)

    scope_c = Scope(org2.id, c.id)
    # C vede solo i dati della propria organizzazione.
    assert _as_principal(dbs.app, scope_c, "SELECT count(*) FROM users") == [(1,)]
    assert _as_principal(dbs.app, scope_c, "SELECT count(*) FROM sessions") == [(1,)]
    assert _as_principal(dbs.app, scope_c, "SELECT count(*) FROM organizations") == [(1,)]
    assert repo.get_user(scope_c, owner.user_id) is None
    assert repo.get_organization(scope_c, owner.organization_id) is None


def test_revoca_impedisce_subito_l_uso(identity) -> None:
    dbs, _, service = identity
    owner = service.bootstrap_owner("A", "test-passphrase-1234")
    assert service.resolve_principal(owner.session_id) == owner

    service.revoke_session(owner)
    with pytest.raises(SessionInvalid):
        service.resolve_principal(owner.session_id)

    # L'effetto è visibile ai dati, non solo al caso d'uso.
    scope_a = Scope(owner.organization_id, owner.user_id)
    revoked = _as_principal(dbs.app, scope_a, "SELECT revoked FROM sessions")
    assert revoked == [(True,)]


def test_principal_non_revoca_la_sessione_altrui(identity) -> None:
    _, engine, service = identity
    owner = service.bootstrap_owner("A", "test-passphrase-1234")
    member = User(new_id(), owner.organization_id, "B", Role.MEMBER, _now())
    assert PostgresUserRepository(engine).add_user(member) is True
    member_session = service.create_session(member)

    store = PostgresSessionStore(engine)
    assert store.revoke(owner.scope, member_session.id) is False
    member_principal = Principal(
        member.id,
        member.organization_id,
        member_session.id,
        member.role,
    )
    assert service.resolve_principal(member_session.id) == member_principal


def test_sessione_presentata_verificata_non_fidata(identity) -> None:
    """L'ID di sessione del client è un riferimento da verificare (§7.1).

    In risoluzione la policy filtra sulla riga presentata: nella stessa
    transazione l'ID valido restituisce i metadati della sessione
    (autenticazione); senza contesto di scope i dati restano invisibili.
    """
    dbs, _, service = identity
    owner = service.bootstrap_owner("A", "test-passphrase-1234")
    estraneo = new_id()

    # ID presentato nella stessa transazione → riga visibile in risoluzione.
    with create_engine(dbs.app).connect() as conn:
        with conn.begin():
            conn.execute(
                text("SELECT set_config('app.session_lookup_id', :id, true)"),
                {"id": str(owner.session_id)},
            )
            rows = [
                tuple(row)
                for row in conn.execute(
                    text("SELECT user_id, organization_id, revoked FROM sessions")
                )
            ]
    assert rows == [(owner.user_id, owner.organization_id, False)]

    # Un ID estraneo presentato non fa vedere nulla.
    with create_engine(dbs.app).connect() as conn:
        with conn.begin():
            conn.execute(
                text("SELECT set_config('app.session_lookup_id', :id, true)"),
                {"id": str(estraneo)},
            )
            rows = [tuple(row) for row in conn.execute(text("SELECT id FROM sessions"))]
    assert rows == []

    # Senza contesto, su transazione pulita, nessun dato.
    assert _query(dbs.app, "SELECT count(*) FROM sessions") == [(0,)]
    assert service.resolve_principal(owner.session_id) == owner


def test_due_bootstrap_concorrenti_un_solo_vincente(identity) -> None:
    """R02: due bootstrap simultanei → un vincente, un Conflict gestito.

    Il passo atomico della porta fa annullare anche l'organizzazione del
    perdente: nessun record orfano, un solo owner (NewRay.md §20.3).
    """
    dbs, _, service = identity
    risultati: list[Principal | Conflict] = []
    barriera = threading.Barrier(2)

    def avvia() -> None:
        barriera.wait()
        try:
            risultati.append(service.bootstrap_owner("P", "test-passphrase-1234"))
        except Conflict as exc:
            risultati.append(exc)

    threads = [threading.Thread(target=avvia) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r in risultati if isinstance(r, Principal)) == 1
    assert sum(1 for r in risultati if isinstance(r, Conflict)) == 1

    # Conteggio come superuser (oltre le policy): niente residui del perdente.
    assert _query(dbs.admin, "SELECT count(*) FROM organizations") == [(1,)]
    assert _query(dbs.admin, "SELECT count(*) FROM users") == [(1,)]
    assert _query(dbs.admin, "SELECT count(*) FROM sessions") == [(1,)]


def test_lookup_owner_richiede_flag_di_transazione(identity) -> None:
    """B-03.2-14: la policy users_owner_lookup autorizza SELECT sull'owner
    solo con il flag di lookup attivo; una transazione priva del flag,
    e senza scope, non vede nulla — nessuna esposizione permanente."""
    dbs, _, service = identity
    service.bootstrap_owner("Ada", "test-passphrase-1234")

    # Senza flag e senza scope: RLS respinge come per le altre letture.
    assert _query(dbs.app, "SELECT count(*) FROM users") == [(0,)]

    # Con il flag di transazione: vede il solo record owner.
    with create_engine(dbs.app).connect() as conn:
        with conn.begin():
            conn.execute(text("SELECT set_config('app.owner_lookup', 'true', true)"))
            rows = [tuple(row) for row in conn.execute(text("SELECT count(*) FROM users"))]
    assert rows == [(1,)]

    # Il flag non trapassa in un'altra transazione della stessa connessione.
    assert _query(dbs.app, "SELECT count(*) FROM users") == [(0,)]


def test_lookup_owner_non_espone_membri_non_owner(identity) -> None:
    """La policy filtra su role='owner': i member non appaiono nel lookup."""
    dbs, engine, service = identity
    owner = service.bootstrap_owner("Ada", "test-passphrase-1234")
    membro = User(new_id(), owner.organization_id, "Bruno", Role.MEMBER, _now())
    PostgresUserRepository(engine).add_user(membro)

    with create_engine(dbs.app).connect() as conn:
        with conn.begin():
            conn.execute(text("SELECT set_config('app.owner_lookup', 'true', true)"))
            names = [
                row.display_name for row in conn.execute(text("SELECT display_name FROM users"))
            ]
    assert names == ["Ada"]


def test_login_reale_apre_una_nuova_sessione(identity) -> None:
    """Bootstrap → logout → login: stessi dati, nuova sessione, RLS reale."""
    _, _, service = identity
    primo = service.bootstrap_owner("Ada", "test-passphrase-1234")
    service.revoke_session(primo)
    with pytest.raises(SessionInvalid):
        service.resolve_principal(primo.session_id)

    ritornato = service.login("Ada", "test-passphrase-1234")
    assert ritornato.user_id == primo.user_id
    assert ritornato.organization_id == primo.organization_id
    assert ritornato.session_id != primo.session_id
    assert service.resolve_principal(ritornato.session_id).user_id == primo.user_id


def test_recupero_credenziale_reale_preserva_id_e_revoca_sessioni(identity) -> None:
    """B-03.2-14 su PostgreSQL reale: stesso user/org ID, la vecchia
    credenziale smette di funzionare, tutte le sessioni sono revocate."""
    dbs, _, service = identity
    primo = service.bootstrap_owner("Ada", "test-passphrase-1234")
    altra_sessione = service.login("Ada", "test-passphrase-1234")

    revocate = service.recover_owner_credential("Ada", "nuova-passphrase-5678")
    assert revocate == 2

    with pytest.raises(InvalidCredentials):
        service.login("Ada", "test-passphrase-1234")
    with pytest.raises(SessionInvalid):
        service.resolve_principal(primo.session_id)
    with pytest.raises(SessionInvalid):
        service.resolve_principal(altra_sessione.session_id)

    recuperato = service.login("Ada", "nuova-passphrase-5678")
    assert recuperato.user_id == primo.user_id
    assert recuperato.organization_id == primo.organization_id

    # Riga reale: stesso ID utente, hash cambiato, nessuna riga duplicata.
    scope = Scope(primo.organization_id, primo.user_id)
    assert _as_principal(dbs.app, scope, "SELECT count(*) FROM users") == [(1,)]


def test_recupero_credenziale_reale_non_tocca_altra_organizzazione(identity) -> None:
    """La revoca di massa resta nello scope RLS del solo owner recuperato."""
    dbs, engine, service = identity
    service.bootstrap_owner("Ada", "test-passphrase-1234")

    org2 = Organization(new_id(), "Altro", _now())
    repo = PostgresUserRepository(engine)
    # MEMBER, non OWNER: il vincolo "un solo owner" è globale
    # all'installazione (NewRay.md §20.3), non per organizzazione.
    estraneo = User(new_id(), org2.id, "Estraneo", Role.MEMBER, _now())
    repo.add_organization(Scope(org2.id, estraneo.id), org2)
    assert repo.add_user(estraneo) is True
    sessione_estranea = service.create_session(estraneo)

    service.recover_owner_credential("Ada", "nuova-passphrase-5678")

    scope_estraneo = Scope(org2.id, estraneo.id)
    revoked = _as_principal(dbs.app, scope_estraneo, "SELECT revoked FROM sessions")
    assert revoked == [(False,)]
    assert service.resolve_principal(sessione_estranea.id).user_id == estraneo.id


def test_recupero_credenziale_reale_su_owner_legacy_senza_hash(identity) -> None:
    """Owner pre-B-03.2-14 (``credential_hash IS NULL``): il recupero
    imposta il primo hash senza passare da un reset del database."""
    dbs, engine, service = identity
    primo = service.bootstrap_owner("Ada", "test-passphrase-1234")
    # Simula lo stato pre-migrazione direttamente sulla riga: nessuna porta
    # di dominio azzera volutamente una credenziale già impostata.
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(primo.user_id)})
        conn.execute(
            text("SELECT set_config('app.organization_id', :o, true)"),
            {"o": str(primo.organization_id)},
        )
        conn.execute(
            text("UPDATE users SET credential_hash = NULL WHERE id = :id"),
            {"id": primo.user_id},
        )
    with pytest.raises(CredentialNotSet):
        service.login("Ada", "qualunque-cosa")

    service.recover_owner_credential("Ada", "nuova-passphrase-5678")
    recuperato = service.login("Ada", "nuova-passphrase-5678")
    assert recuperato.user_id == primo.user_id

    scope = Scope(primo.organization_id, primo.user_id)
    assert _as_principal(dbs.app, scope, "SELECT count(*) FROM organizations") == [(1,)]
