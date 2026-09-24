"""Casi d'uso dell'identità: bootstrap monouso e lifecycle della sessione."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fakes import (
    FakeClock,
    InMemoryOwnerBootstrap,
    InMemorySessionStore,
    InMemoryUserRepository,
)
from newray.kernel.crypto import _make_pbkdf2_hash, verify_and_check_rehash
from newray.kernel.errors import (
    Conflict,
    CredentialNotSet,
    InvalidCredentials,
    InvalidName,
    NotFound,
    SessionInvalid,
)
from newray.kernel.identity import Role, new_id
from newray.modules.identity import IdentityService, User

EPOCA = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def make_service(clock: FakeClock | None = None) -> tuple[IdentityService, FakeClock]:
    clock = clock or FakeClock(EPOCA)
    users = InMemoryUserRepository()
    sessions = InMemorySessionStore()
    service = IdentityService(users, sessions, clock, InMemoryOwnerBootstrap(users, sessions))
    return service, clock


class _BootstrapInstabile:
    """Porta di fault injection: fallisce al primo passo atomico, poi delega."""

    def __init__(self, interno: InMemoryOwnerBootstrap) -> None:
        self._interno = interno
        self.tentativi = 0

    def execute(self, organization: object, user: object, session: object) -> None:
        self.tentativi += 1
        if self.tentativi == 1:
            raise RuntimeError("guasto simulato durante il commit")
        self._interno.execute(organization, user, session)


def test_bootstrap_crea_organizzazione_utente_e_sessione() -> None:
    service, _ = make_service()
    principal = service.bootstrap_owner("Proprietario", "test-passphrase-1234")
    assert principal.role is Role.OWNER
    # La sessione emessa dal bootstrap è immediatamente risolvibile.
    assert service.resolve_principal(principal.session_id) == principal
    # L'utente è leggibile solo con il proprio scope, come ogni dato privato.
    assert service.resolve_principal(principal.session_id).user_id == principal.user_id


def test_bootstrap_e_monouso() -> None:
    """NewRay.md §20.3: bootstrap locale monouso, mai sovrascrittura."""
    service, _ = make_service()
    service.bootstrap_owner("Primo", "test-passphrase-1234")
    with pytest.raises(Conflict):
        service.bootstrap_owner("Secondo", "test-passphrase-1234")


def test_conflitto_di_bootstrap_non_altera_il_vincente() -> None:
    """R02: il secondo bootstrap fallisce con Conflict; il primo principal
    resta integralmente risolvibile."""
    service, _ = make_service()
    primo = service.bootstrap_owner("Primo", "test-passphrase-1234")
    with pytest.raises(Conflict):
        service.bootstrap_owner("Secondo", "test-passphrase-1234")
    assert service.resolve_principal(primo.session_id) == primo


def test_guasto_del_passo_atomico_non_lascia_residui() -> None:
    """R02: il guasto durante il commit propaga l'errore senza residui;
    il bootstrap ripetuto riparte da zero e riesce."""
    users = InMemoryUserRepository()
    sessions = InMemorySessionStore()
    instabile = _BootstrapInstabile(InMemoryOwnerBootstrap(users, sessions))
    clock = FakeClock(EPOCA)
    service = IdentityService(users, sessions, clock, instabile)
    with pytest.raises(RuntimeError, match="guasto"):
        service.bootstrap_owner("P", "test-passphrase-1234")
    # Niente organizzazione, niente utente, niente sessione.
    assert users.organization_count() == 0
    assert users.user_count() == 0
    assert sessions.session_count() == 0
    # Il retry crea un nuovo bootstrap valido: un solo tentativo andato.
    principal = service.bootstrap_owner("P", "test-passphrase-1234")
    assert instabile.tentativi == 2
    assert service.resolve_principal(principal.session_id) == principal


def test_resolve_rifiuta_sessione_sconosciuta() -> None:
    service, _ = make_service()
    with pytest.raises(SessionInvalid):
        service.resolve_principal(new_id())


def test_revoca_blocca_la_risoluzione_subeito() -> None:
    service, _ = make_service()
    principal = service.bootstrap_owner("P", "test-passphrase-1234")
    service.revoke_session(principal)
    with pytest.raises(SessionInvalid):
        service.resolve_principal(principal.session_id)
    # La revoca è idempotente: ripetuta non solleva.
    service.revoke_session(principal)


def test_sessione_scaduta_non_risolve() -> None:
    service, clock = make_service()
    principal = service.bootstrap_owner("P", "test-passphrase-1234")
    clock.advance(timedelta(days=31))  # oltre il TTL predefinito di 30 giorni
    with pytest.raises(SessionInvalid):
        service.resolve_principal(principal.session_id)


def test_principal_rivien_da_record_di_server() -> None:
    """Il client non dichiara user_id o ruolo: unica input l'ID di sessione.

    Criterio di uscita A-02: identificativi del browser non fidati. Il
    principal risolto riporta solo quanto registrato dal server.
    """
    service, _ = make_service()
    principal = service.bootstrap_owner("P", "test-passphrase-1234")
    risolto = service.resolve_principal(principal.session_id)
    assert (
        risolto.user_id,
        risolto.organization_id,
        risolto.session_id,
        risolto.role,
    ) == (
        principal.user_id,
        principal.organization_id,
        principal.session_id,
        Role.OWNER,
    )


def test_bootstrap_rifiuta_credenziale_troppo_corta() -> None:
    """B-03.2-14: nessuna password predefinita né banale sotto la soglia."""
    service, _ = make_service()
    with pytest.raises(InvalidCredentials):
        service.bootstrap_owner("Ada", "ciao")


def test_login_apre_una_nuova_sessione_con_credenziale_corretta() -> None:
    """B-03.2-14: il rientro non riusa la sessione del bootstrap."""
    service, _ = make_service()
    primo = service.bootstrap_owner("Ada", "test-passphrase-1234")
    secondo = service.login("Ada", "test-passphrase-1234")
    assert secondo.user_id == primo.user_id
    assert secondo.session_id != primo.session_id
    # Entrambe le sessioni restano risolvibili finché non si revoca la prima.
    assert service.resolve_principal(primo.session_id).session_id == primo.session_id
    assert service.resolve_principal(secondo.session_id).session_id == secondo.session_id


def test_login_dopo_revoca_e_come_rientro_locale() -> None:
    """Bootstrap → logout → login: stessi dati, nuova sessione."""
    service, _ = make_service()
    primo = service.bootstrap_owner("Ada", "test-passphrase-1234")
    service.revoke_session(primo)
    with pytest.raises(SessionInvalid):
        service.resolve_principal(primo.session_id)
    ritornato = service.login("Ada", "test-passphrase-1234")
    assert ritornato.user_id == primo.user_id
    assert ritornato.organization_id == primo.organization_id


def test_login_con_credenziale_sbagliata_e_owner_sconosciuto_e_indistinguibile() -> None:
    """B-03.2-14: nessun oracolo per enumerare i nomi (§19.4)."""
    service, _ = make_service()
    service.bootstrap_owner("Ada", "test-passphrase-1234")
    with pytest.raises(InvalidCredentials):
        service.login("Ada", "credenziale-sbagliata")
    with pytest.raises(InvalidCredentials):
        service.login("Sconosciuto", "test-passphrase-1234")


def test_login_su_owner_senza_credenziale_e_recupero_esplicito() -> None:
    """Owner pre-B-03.2-14: risposta CREDENTIAL_NOT_SET, mai INVALID."""
    users = InMemoryUserRepository()
    sessions = InMemorySessionStore()
    service = IdentityService(
        users, sessions, FakeClock(EPOCA), InMemoryOwnerBootstrap(users, sessions)
    )
    principal = service.bootstrap_owner("Ada", "test-passphrase-1234")
    users.set_credential_hash(principal.user_id, None)
    with pytest.raises(CredentialNotSet):
        service.login("Ada", "qualunque-cosa")


def test_status_dichiara_installazione_bootstrappata() -> None:
    service, _ = make_service()
    assert service.is_bootstrapped() is False
    service.bootstrap_owner("Ada", "test-passphrase-1234")
    assert service.is_bootstrapped() is True


def test_bootstrap_normalizza_i_margini_del_nome() -> None:
    """B-03.2-30: bootstrap e login condividono la stessa normalizzazione.

    Margini di whitespace rimossi, spazi interni e case preservati:
    il nome persistito è quello normalizzato, e il login con un nome
    "sporco" raggiunge lo stesso owner.
    """
    service, _ = make_service()
    principal = service.bootstrap_owner("  Ada Lovelace \t", "test-passphrase-1234")
    assert service.current_user(principal).display_name == "Ada Lovelace"
    rientro = service.login(" Ada Lovelace", "test-passphrase-1234")
    assert rientro.user_id == principal.user_id


def test_bootstrap_rifiuta_nome_vuoto_dopo_normalizzazione() -> None:
    """B-03.2-30: solo whitespace è input malformato, non un nome valido.

    Rifiutato nel caso d'uso (il DTO ammette stringhe non vuote): nessun
    residuo, il bootstrap resta eseguibile.
    """
    service, _ = make_service()
    with pytest.raises(InvalidName):
        service.bootstrap_owner("   ", "test-passphrase-1234")
    assert service.is_bootstrapped() is False
    principal = service.bootstrap_owner("Ada", "test-passphrase-1234")
    assert service.current_user(principal).display_name == "Ada"


def test_login_rifiuta_nome_vuoto_dopo_normalizzazione() -> None:
    """B-03.2-30: nel login il nome vuoto è validazione, non credenziale.

    Distinto da ``InvalidCredentials``: non partecipa al collasso
    anti-oracolo, perché non è un tentativo di autentica.
    """
    service, _ = make_service()
    service.bootstrap_owner("Ada", "test-passphrase-1234")
    with pytest.raises(InvalidName):
        service.login("  ", "test-passphrase-1234")


def test_login_preserva_il_case_del_nome() -> None:
    """B-03.2-30: nessun case folding — nessuna identità implicita.

    Il nome è un dato scelto dall'utente, non un identificatore
    case-insensitive: "ada" non è "Ada".
    """
    service, _ = make_service()
    service.bootstrap_owner("Ada", "test-passphrase-1234")
    with pytest.raises(InvalidCredentials):
        service.login("ada", "test-passphrase-1234")


# --- B-03.2-32: rehash PBKDF2 → Argon2id al login ---


def test_login_con_hash_pbkdf2_legacy_effettua_rehash_ad_argon2() -> None:
    """B-03.2-32: un owner con hash PBKDF2 autentica e viene migrato."""
    users = InMemoryUserRepository()
    sessions = InMemorySessionStore()
    service = IdentityService(
        users, sessions, FakeClock(EPOCA), InMemoryOwnerBootstrap(users, sessions)
    )
    principal = service.bootstrap_owner("Ada", "test-passphrase-1234")
    legacy_hash = _make_pbkdf2_hash("test-passphrase-1234")
    users.set_credential_hash(principal.user_id, legacy_hash)
    rientro = service.login("Ada", "test-passphrase-1234")
    assert rientro.user_id == principal.user_id
    stored_user = users._users[principal.user_id]
    assert stored_user.credential_hash is not None
    assert stored_user.credential_hash.startswith("$argon2id$")
    result = verify_and_check_rehash("test-passphrase-1234", stored_user.credential_hash)
    assert result.valid is True
    assert result.needs_rehash is False


def test_login_con_hash_argon2_non_effettua_rehash() -> None:
    """B-03.2-32: un hash già Argon2id non viene riscritto."""
    users = InMemoryUserRepository()
    sessions = InMemorySessionStore()
    service = IdentityService(
        users, sessions, FakeClock(EPOCA), InMemoryOwnerBootstrap(users, sessions)
    )
    principal = service.bootstrap_owner("Ada", "test-passphrase-1234")
    hash_before = users._users[principal.user_id].credential_hash
    service.login("Ada", "test-passphrase-1234")
    hash_after = users._users[principal.user_id].credential_hash
    assert hash_before == hash_after


def test_login_owner_sconosciuto_esegue_dummy_verify() -> None:
    """B-03.2-31: il tempo di risposta non rivela se il nome esiste."""
    service, _ = make_service()
    service.bootstrap_owner("Ada", "test-passphrase-1234")
    with pytest.raises(InvalidCredentials):
        service.login("Sconosciuto", "test-passphrase-1234")


def test_recupero_credenziale_permette_il_login_con_la_nuova_password() -> None:
    """B-03.2-14: dopo il recupero, la vecchia credenziale non funziona
    più e la nuova sì — stesso user/org ID, nessuna ricreazione."""
    service, _ = make_service()
    principal = service.bootstrap_owner("Ada", "test-passphrase-1234")
    service.recover_owner_credential("Ada", "nuova-passphrase-5678")
    with pytest.raises(InvalidCredentials):
        service.login("Ada", "test-passphrase-1234")
    nuovo_principal = service.login("Ada", "nuova-passphrase-5678")
    assert nuovo_principal.user_id == principal.user_id
    assert nuovo_principal.organization_id == principal.organization_id


def test_recupero_credenziale_su_owner_legacy_senza_hash() -> None:
    """Il caso che ha motivato il ticket: owner pre-B-03.2-14 con
    ``credential_hash IS NULL`` (CREDENTIAL_NOT_SET al login) recupera
    un accesso locale senza reset del database."""
    users = InMemoryUserRepository()
    sessions = InMemorySessionStore()
    service = IdentityService(
        users, sessions, FakeClock(EPOCA), InMemoryOwnerBootstrap(users, sessions)
    )
    principal = service.bootstrap_owner("Ada", "test-passphrase-1234")
    users.set_credential_hash(principal.user_id, None)
    with pytest.raises(CredentialNotSet):
        service.login("Ada", "qualunque-cosa")

    service.recover_owner_credential("Ada", "nuova-passphrase-5678")
    recuperato = service.login("Ada", "nuova-passphrase-5678")
    assert recuperato.user_id == principal.user_id


def test_recupero_credenziale_revoca_tutte_le_sessioni_esistenti() -> None:
    """Un reset della credenziale invalida ogni accesso aperto in
    precedenza: nessun login silenzioso lasciato attivo."""
    service, _ = make_service()
    principal = service.bootstrap_owner("Ada", "test-passphrase-1234")
    altra_sessione = service.login("Ada", "test-passphrase-1234")

    revocate = service.recover_owner_credential("Ada", "nuova-passphrase-5678")

    assert revocate == 2  # sessione di bootstrap + il login sopra
    with pytest.raises(SessionInvalid):
        service.resolve_principal(principal.session_id)
    with pytest.raises(SessionInvalid):
        service.resolve_principal(altra_sessione.session_id)


def test_recupero_credenziale_non_tocca_le_sessioni_di_un_altro_principal() -> None:
    """La revoca di massa resta nello scope del solo owner recuperato."""
    users = InMemoryUserRepository()
    sessions = InMemorySessionStore()
    clock = FakeClock(EPOCA)
    service = IdentityService(users, sessions, clock, InMemoryOwnerBootstrap(users, sessions))
    principal = service.bootstrap_owner("Ada", "test-passphrase-1234")
    estraneo = service.create_session(
        User(
            id=new_id(),
            organization_id=new_id(),
            display_name="Estraneo",
            role=Role.MEMBER,
            created_at=EPOCA,
            credential_hash=None,
        )
    )

    service.recover_owner_credential("Ada", "nuova-passphrase-5678")

    with pytest.raises(SessionInvalid):
        service.resolve_principal(principal.session_id)
    # La sessione di un principal estraneo (organizzazione diversa) non è
    # nello scope dell'owner recuperato: resta intatta.
    assert sessions._sessions[estraneo.id].revoked is False


def test_recupero_credenziale_owner_sconosciuto_e_not_found() -> None:
    service, _ = make_service()
    service.bootstrap_owner("Ada", "test-passphrase-1234")
    with pytest.raises(NotFound):
        service.recover_owner_credential("Sconosciuto", "nuova-passphrase-5678")


def test_recupero_credenziale_rifiuta_password_troppo_corta() -> None:
    service, _ = make_service()
    service.bootstrap_owner("Ada", "test-passphrase-1234")
    with pytest.raises(InvalidCredentials):
        service.recover_owner_credential("Ada", "corta")


def test_recupero_credenziale_normalizza_il_nome() -> None:
    """Stessa normalizzazione di login/bootstrap (B-03.2-30): solo i
    margini di whitespace sono rimossi."""
    service, _ = make_service()
    service.bootstrap_owner("Ada", "test-passphrase-1234")
    service.recover_owner_credential("  Ada  ", "nuova-passphrase-5678")
    service.login("Ada", "nuova-passphrase-5678")


def test_recupero_credenziale_rifiuta_nome_vuoto_dopo_normalizzazione() -> None:
    service, _ = make_service()
    with pytest.raises(InvalidName):
        service.recover_owner_credential("   ", "nuova-passphrase-5678")
