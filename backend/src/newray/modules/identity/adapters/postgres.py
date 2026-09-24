"""Adapter PostgreSQL del modulo identity (NewRay.md §7.3; ADR 0002).

Il ruolo applicativo (``newray_app``) non è proprietario delle tabelle e
non ha superuser né BYPASSRLS: la RLS filtra ogni riga. Il contesto è
impostato per transazione (``set_config`` locale):

- letture/scritture di scope: ``app.user_id`` + ``app.organization_id``;
- risoluzione/revoca di sessione: ``app.session_lookup_id`` (ID opaco
  presentato, verificato dalla policy, mai fidato — NewRay.md §7.1).

Il SQL resta in questo file: dominio e applicazione non conoscono né
SQLAlchemy né PostgreSQL.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from newray.kernel.errors import Conflict
from newray.kernel.identity import Role, Scope

from ..domain import Organization, Session, User


def _set_scope_context(conn: Connection, scope: Scope) -> None:
    """Contesto di scope per la transazione corrente (locale, auto-pulito)."""
    conn.execute(text("SELECT set_config('app.user_id', :v, true)"), {"v": str(scope.user_id)})
    conn.execute(
        text("SELECT set_config('app.organization_id', :v, true)"),
        {"v": str(scope.organization_id)},
    )


def _set_lookup_context(conn: Connection, session_id: uuid.UUID) -> None:
    """Contesto di risoluzione: l'ID opaco presentato, verificato da RLS."""
    conn.execute(
        text("SELECT set_config('app.session_lookup_id', :v, true)"),
        {"v": str(session_id)},
    )


def _set_owner_lookup_flag(conn: Connection) -> None:
    """Autorizza la SELECT dell'owner in stato pre-autenticato (login e
    status pubblico dell'installazione). La policy RLS dedicata
    (migrazione 0005) restringe già la lettura a ``role = 'owner'``:
    questo flag scoped alla transazione firma l'intenzione esplicita
    del caso d'uso, così ogni altra query fuori dalle porte identity
    non lo abilita per errore (B-03.2-14 / R13)."""
    conn.execute(text("SELECT set_config('app.owner_lookup', 'true', true)"))


class PostgresUserRepository:
    """``UserRepository`` su PostgreSQL: scope obbligatorio, RLS di barriera."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add_organization(self, scope: Scope, organization: Organization) -> None:
        with self._engine.begin() as conn:
            _set_scope_context(conn, scope)
            conn.execute(
                text(
                    "INSERT INTO organizations (id, display_name, created_at) "
                    "VALUES (:id, :name, :created)"
                ),
                {
                    "id": organization.id,
                    "name": organization.display_name,
                    "created": organization.created_at,
                },
            )

    def get_organization(self, scope: Scope, organization_id: uuid.UUID) -> Organization | None:
        with self._engine.connect() as conn:
            _set_scope_context(conn, scope)
            row = conn.execute(
                text("SELECT id, display_name, created_at FROM organizations WHERE id = :id"),
                {"id": organization_id},
            ).first()
        if row is None:
            return None
        return Organization(id=row.id, display_name=row.display_name, created_at=row.created_at)

    def add_user(self, user: User) -> bool:
        """Inserimento atomico: un solo owner per installazione.

        Il vincolo parziale (migration 0001) rende ``False`` il secondo
        bootstrap; il contesto di scope deriva dal record, non dal client.
        """
        with self._engine.begin() as conn:
            _set_scope_context(conn, Scope(user.organization_id, user.id))
            result = conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, organization_id, display_name, role, created_at, credential_hash) "
                    "VALUES (:id, :org, :name, :role, :created, :hash) "
                    "ON CONFLICT ((0)) WHERE role = 'owner' DO NOTHING"
                ),
                {
                    "id": user.id,
                    "org": user.organization_id,
                    "name": user.display_name,
                    "role": user.role.value,
                    "created": user.created_at,
                    "hash": user.credential_hash,
                },
            )
        return result.rowcount == 1

    def get_user(self, scope: Scope, user_id: uuid.UUID) -> User | None:
        """Lettura privata: lo scope imposta il contesto RLS (NewRay.md §7.3)."""
        with self._engine.connect() as conn:
            _set_scope_context(conn, scope)
            row = conn.execute(
                text(
                    "SELECT id, organization_id, display_name, role, created_at, "
                    "credential_hash FROM users WHERE id = :id"
                ),
                {"id": user_id},
            ).first()
        if row is None:
            return None
        return User(
            id=row.id,
            organization_id=row.organization_id,
            display_name=row.display_name,
            role=Role(row.role),
            created_at=row.created_at,
            credential_hash=row.credential_hash,
        )

    def find_owner_by_display_name(self, display_name: str) -> User | None:
        """Lookup pre-auth: la policy RLS dedicata autorizza SELECT sui
        soli record con ``role = 'owner'`` e con il flag di lookup attivo
        nella transazione corrente. Il display_name viene passato
        parametricamente (mai concatenato): stringhe ostili non alterano
        il SQL né la RLS."""
        with self._engine.connect() as conn:
            _set_owner_lookup_flag(conn)
            row = conn.execute(
                text(
                    "SELECT id, organization_id, display_name, role, created_at, "
                    "credential_hash FROM users "
                    "WHERE role = 'owner' AND display_name = :name"
                ),
                {"name": display_name},
            ).first()
        if row is None:
            return None
        return User(
            id=row.id,
            organization_id=row.organization_id,
            display_name=row.display_name,
            role=Role(row.role),
            created_at=row.created_at,
            credential_hash=row.credential_hash,
        )

    def update_credential_hash(self, scope: Scope, new_hash: str) -> None:
        """Rehash atomico (PBKDF2 → Argon2id, B-03.2-32) o recupero locale
        (B-03.2-14). Il flag di lookup autorizza solo la SELECT pre-auth:
        l'UPDATE è governato dalla policy generale ``users_isolation``,
        che richiede lo scope della transazione (bug osservato su
        PostgreSQL reale — il flag da solo produce un UPDATE a 0 righe,
        mai segnalato perché il chiamante non controllava rowcount)."""
        with self._engine.begin() as conn:
            _set_scope_context(conn, scope)
            result = conn.execute(
                text(
                    "UPDATE users SET credential_hash = :hash "
                    "WHERE id = :id AND organization_id = :org AND role = 'owner'"
                ),
                {"hash": new_hash, "id": scope.user_id, "org": scope.organization_id},
            )
            if not result.rowcount:
                # Invariante del chiamante violata (utente assente/non
                # owner/fuori scope): mai un successo silenzioso su un
                # aggiornamento di sicurezza come la credenziale.
                raise RuntimeError(
                    "update_credential_hash: nessuna riga aggiornata per lo scope indicato"
                )

    def has_owner(self) -> bool:
        """Presenza di un owner: stessa policy di ``find_owner_*``."""
        with self._engine.connect() as conn:
            _set_owner_lookup_flag(conn)
            row = conn.execute(text("SELECT 1 FROM users WHERE role = 'owner' LIMIT 1")).first()
        return row is not None


class PostgresSessionStore:
    """``SessionStore`` su PostgreSQL: sessioni opache, revoca immediata."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(self, session: Session) -> None:
        with self._engine.begin() as conn:
            _set_scope_context(conn, Scope(session.organization_id, session.user_id))
            conn.execute(
                text(
                    "INSERT INTO sessions "
                    "(id, user_id, organization_id, created_at, expires_at, revoked) "
                    "VALUES (:id, :user, :org, :created, :expires, :revoked)"
                ),
                {
                    "id": session.id,
                    "user": session.user_id,
                    "org": session.organization_id,
                    "created": session.created_at,
                    "expires": session.expires_at,
                    "revoked": session.revoked,
                },
            )

    def find_for_resolution(self, session_id: uuid.UUID) -> Session | None:
        """Lettura in risoluzione: l'ID opaco presentato è verificato, non fidato."""
        with self._engine.connect() as conn:
            _set_lookup_context(conn, session_id)
            row = conn.execute(
                text(
                    "SELECT id, user_id, organization_id, created_at, expires_at, revoked "
                    "FROM sessions WHERE id = :id"
                ),
                {"id": session_id},
            ).first()
        if row is None:
            return None
        return Session(
            id=row.id,
            user_id=row.user_id,
            organization_id=row.organization_id,
            created_at=row.created_at,
            expires_at=row.expires_at,
            revoked=row.revoked,
        )

    def revoke(self, scope: Scope, session_id: uuid.UUID) -> bool:
        """Revoca idempotente soltanto nello scope autenticato."""
        with self._engine.begin() as conn:
            _set_scope_context(conn, scope)
            result = conn.execute(
                text(
                    "UPDATE sessions SET revoked = TRUE "
                    "WHERE id = :id AND user_id = :user AND organization_id = :org "
                    "AND revoked = FALSE"
                ),
                {
                    "id": session_id,
                    "user": scope.user_id,
                    "org": scope.organization_id,
                },
            )
        return result.rowcount is not None and result.rowcount > 0

    def revoke_all_for_user(self, scope: Scope) -> int:
        """Revoca massiva nello scope dell'utente (recupero credenziale,
        B-03.2-14): stesso predicato di ``revoke``, senza filtro per ID."""
        with self._engine.begin() as conn:
            _set_scope_context(conn, scope)
            result = conn.execute(
                text(
                    "UPDATE sessions SET revoked = TRUE "
                    "WHERE user_id = :user AND organization_id = :org AND revoked = FALSE"
                ),
                {"user": scope.user_id, "org": scope.organization_id},
            )
        return result.rowcount or 0


class PostgresOwnerBootstrap:
    """``OwnerBootstrap`` su PostgreSQL: i tre record in una sola transazione.

    Organizzazione, proprietario e sessione corrono nella stessa
    transazione (``engine.begin``): il vincolo "un solo owner"
    (migration 0001) rende ``rowcount == 0`` il secondo bootstrap; a quel
    punto si propaga ``Conflict`` e il contesto di transazione annulla
    anche l'organizzazione appena inserita — nessun residuo, un solo
    vincente anche in concorrenza (R02).
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def execute(self, organization: Organization, user: User, session: Session) -> None:
        with self._engine.begin() as conn:
            # Il contesto di scope deriva dai record, non dal client.
            _set_scope_context(conn, Scope(user.organization_id, user.id))
            conn.execute(
                text(
                    "INSERT INTO organizations (id, display_name, created_at) "
                    "VALUES (:id, :name, :created)"
                ),
                {
                    "id": organization.id,
                    "name": organization.display_name,
                    "created": organization.created_at,
                },
            )
            result = conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, organization_id, display_name, role, created_at, credential_hash) "
                    "VALUES (:id, :org, :name, :role, :created, :hash) "
                    "ON CONFLICT ((0)) WHERE role = 'owner' DO NOTHING"
                ),
                {
                    "id": user.id,
                    "org": user.organization_id,
                    "name": user.display_name,
                    "role": user.role.value,
                    "created": user.created_at,
                    "hash": user.credential_hash,
                },
            )
            if result.rowcount != 1:
                raise Conflict("il proprietario è già stato creato")
            conn.execute(
                text(
                    "INSERT INTO sessions "
                    "(id, user_id, organization_id, created_at, expires_at, revoked) "
                    "VALUES (:id, :user, :org, :created, :expires, :revoked)"
                ),
                {
                    "id": session.id,
                    "user": session.user_id,
                    "org": session.organization_id,
                    "created": session.created_at,
                    "expires": session.expires_at,
                    "revoked": session.revoked,
                },
            )
