"""Casi d'uso dell'identità (NewRay.md §§7, 20.3).

Il principal è sempre risolto dal server a partire dalla sessione: nessun
dato del client (header, ID nel payload) partecipa alla risoluzione. I
casi d'uso sono indipendenti da FastAPI e SQL: il trasporto traduce il
confine (``interfaces/http/middleware``), l'adapter arriva con A-03.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from newray.kernel.clock import Clock
from newray.kernel.crypto import (
    InvalidCredentialFormat,
    dummy_verify,
    hash_credential,
    verify_and_check_rehash,
)
from newray.kernel.errors import (
    CredentialNotSet,
    InvalidCredentials,
    InvalidName,
    NotFound,
    SessionInvalid,
)
from newray.kernel.identity import Principal, Role, Scope, new_id

from .domain import Organization, Session, User
from .ports import OwnerBootstrap, SessionStore, UserRepository

#: Scadenza predefinita della sessione personale; la configurazione risolta
#: (A-07) potrà solo restringerla.
DEFAULT_SESSION_TTL = timedelta(days=30)

#: Nome dell'organizzazione personale: un'unica installazione locale
#: (NewRay.md §2.2).
PERSONAL_ORG_NAME = "NewRay"


def normalize_display_name(raw: str) -> str:
    """Normalizzazione unica del nome del proprietario (B-03.2-30).

    Rimuove i soli margini di whitespace (paste accidentali, form che
    aggiungono spazi) e preserva gli spazi interni: il nome è un dato
    scelto dall'utente, non un identificatore case-insensitive — nessun
    case folding, nessuna identità alternativa implicita. La validità
    (non vuoto dopo normalizzazione) è verificata dal caso d'uso con
    ``InvalidName``, oltre al limite del DTO.
    """
    return raw.strip()


class IdentityService:
    """Casi d'uso: bootstrap del proprietario, sessioni, risoluzione principal."""

    def __init__(
        self,
        users: UserRepository,
        sessions: SessionStore,
        clock: Clock,
        bootstrap: OwnerBootstrap,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._clock = clock
        self._bootstrap = bootstrap

    def bootstrap_owner(self, display_name: str, credential: str) -> Principal:
        """Crea organizzazione, proprietario e sessione, una sola volta.

        Bootstrap locale monouso (NewRay.md §20.3): i tre record e il
        record hash della credenziale vengono persistiti in una sola
        transazione atomica (``OwnerBootstrap``, R02); se il
        proprietario esiste, ``Conflict`` dopo l'annullamento completo —
        niente stato parziale né organizzazione orfana. Mai una password
        predefinita condivisa: la credenziale è obbligatoria (minimo di
        composizione applicato dal kernel, ``InvalidCredentialFormat``
        tradotto in ``InvalidCredentials`` — messaggio localizzabile,
        codice pubblico stabile).
        """
        try:
            credential_hash = hash_credential(credential)
        except InvalidCredentialFormat as exc:
            raise InvalidCredentials(str(exc)) from exc
        name = normalize_display_name(display_name)
        if not name:
            raise InvalidName("il nome del proprietario non può essere vuoto")
        now = self._clock.now()
        organization = Organization(id=new_id(), display_name=PERSONAL_ORG_NAME, created_at=now)
        user = User(
            id=new_id(),
            organization_id=organization.id,
            display_name=name,
            role=Role.OWNER,
            created_at=now,
            credential_hash=credential_hash,
        )
        session = self._build_session(user)
        self._bootstrap.execute(organization, user, session)
        return Principal(
            user_id=user.id,
            organization_id=user.organization_id,
            session_id=session.id,
            role=user.role,
        )

    def login(self, display_name: str, credential: str) -> Principal:
        """Autentica il proprietario esistente e apre una nuova sessione.

        L'ID di sessione è nuovo ad ogni login: nessun riuso di sessioni
        precedenti, niente "sostituto d'identità" a partire dal solo
        nome (B-03.2-14). L'assenza dell'owner e la credenziale sbagliata
        collassano in un unico ``InvalidCredentials`` con una verifica
        dummy a tempo uniforme per non trasformare l'endpoint in un
        oracolo per enumerare i nomi (B-03.2-31, NewRay.md §19.4).
        Un owner senza record hash — installazione pre-migrazione —
        risponde ``CredentialNotSet`` per abilitare recupero esplicito.

        Il nome è normalizzato con la stessa regola del bootstrap
        (B-03.2-30): margini di whitespace rimossi, spazi interni e
        case preservati. Un nome vuoto dopo normalizzazione è un input
        malformato (``InvalidName``), non un tentativo di credenziale.
        """
        name = normalize_display_name(display_name)
        if not name:
            raise InvalidName("il nome del proprietario non può essere vuoto")
        user = self._users.find_owner_by_display_name(name)
        if user is None:
            dummy_verify()
            raise InvalidCredentials("credenziali non valide")
        if user.credential_hash is None:
            raise CredentialNotSet(
                "l'installazione esistente non ha una credenziale locale: "
                "impostarla prima del rientro"
            )
        result = verify_and_check_rehash(credential, user.credential_hash)
        if not result.valid:
            raise InvalidCredentials("credenziali non valide")
        if result.needs_rehash:
            new_hash = hash_credential(credential)
            self._users.update_credential_hash(Scope(user.organization_id, user.id), new_hash)
        session = self._open_session(user)
        return Principal(
            user_id=user.id,
            organization_id=user.organization_id,
            session_id=session.id,
            role=user.role,
        )

    def recover_owner_credential(self, display_name: str, new_credential: str) -> int:
        """Recupero locale della credenziale del proprietario (B-03.2-14).

        Percorso riservato a chi ha già autorità locale sull'installazione
        (accesso al processo/DB, es. il comando
        ``newray.bootstrap.recover_credential``): non esiste un percorso
        web equivalente. Il nome è solo la chiave di lookup, non
        un'autenticazione — l'autorità è dimostrata dall'accesso locale
        stesso, mai da una password condivisa o da un'assegnazione non
        autenticata. Preserva user/org ID e ogni altro dato: cambia solo
        l'hash della credenziale, mai una ricreazione dell'owner né un
        reset del database. Tutte le sessioni esistenti vengono revocate:
        un reset della credenziale invalida ogni accesso aperto in
        precedenza, mai un login silenzioso lasciato attivo.
        """
        name = normalize_display_name(display_name)
        if not name:
            raise InvalidName("il nome del proprietario non può essere vuoto")
        user = self._users.find_owner_by_display_name(name)
        if user is None:
            raise NotFound("proprietario non trovato")
        try:
            credential_hash = hash_credential(new_credential)
        except InvalidCredentialFormat as exc:
            raise InvalidCredentials(str(exc)) from exc
        scope = Scope(user.organization_id, user.id)
        self._users.update_credential_hash(scope, credential_hash)
        return self._sessions.revoke_all_for_user(scope)

    def is_bootstrapped(self) -> bool:
        """Stato pubblico dell'installazione (`GET /session/status`)."""
        return self._users.has_owner()

    def create_session(self, user: User, ttl: timedelta = DEFAULT_SESSION_TTL) -> Session:
        """Apre una nuova sessione per un utente già registrato.

        Usato dai futuri login (account locali/SSO, incrementi successivi);
        in A-02 è il caso d'uso della rotazione delle sessioni.
        """
        return self._open_session(user, ttl)

    def resolve_principal(self, session_id: uuid.UUID) -> Principal:
        """Risoluzione server-side: la sola input è l'ID di sessione opaco.

        Assente, scaduta o revocata → ``SessionInvalid``; nessun fallback
        su identificativi forniti dal client (NewRay.md §7.1).
        """
        session = self._sessions.find_for_resolution(session_id)
        if session is None:
            raise SessionInvalid("sessione sconosciuta")
        user = self._users.get_user(
            Scope(session.organization_id, session.user_id), session.user_id
        )
        if user is None:
            raise SessionInvalid("sessione non associata a un utente valido")
        if not session.is_active(self._clock.now()):
            raise SessionInvalid("sessione scaduta o revocata")
        return Principal(
            user_id=user.id,
            organization_id=user.organization_id,
            session_id=session.id,
            role=user.role,
        )

    def current_user(self, principal: Principal) -> User:
        """Dati dell'utente del principal, letti nel proprio scope (§7.3).

        Il principal è già stato risolto dal server a partire dalla
        sessione: la chiamata non può essere devissata verso un altro
        utente. Utente assente → ``SessionInvalid``.
        """
        user = self._users.get_user(principal.scope, principal.user_id)
        if user is None:
            raise SessionInvalid("utente associato alla sessione assente")
        return user

    def revoke_session(self, principal: Principal) -> None:
        """Revoca la sessione corrente nello scope risolto dal server."""
        self._sessions.revoke(principal.scope, principal.session_id)

    def _open_session(self, user: User, ttl: timedelta = DEFAULT_SESSION_TTL) -> Session:
        session = self._build_session(user, ttl)
        self._sessions.save(session)
        return session

    def _build_session(self, user: User, ttl: timedelta = DEFAULT_SESSION_TTL) -> Session:
        """Costruisce la sessione senza persistere: la scrittura passa
        dall'unico punto transazionale competente per ciascun caso d'uso."""
        now = self._clock.now()
        return Session(
            id=new_id(),
            user_id=user.id,
            organization_id=user.organization_id,
            created_at=now,
            expires_at=now + ttl,
        )
