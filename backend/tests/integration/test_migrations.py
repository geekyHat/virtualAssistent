"""Migrazioni su PostgreSQL reale: schema, RLS, ruoli e downgrade (A-03)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from conftest import DatabaseHandles
from sqlalchemy import create_engine, text

from newray.infrastructure.database import create_engine as create_db_engine
from newray.kernel.clock import SystemClock
from newray.kernel.identity import Principal, Role, new_id
from newray.modules.conversations import ConversationService
from newray.modules.conversations.adapters.postgres import (
    PostgresConversationRepository,
    PostgresMessageStore,
)
from newray.modules.identity.adapters.postgres import PostgresUserRepository

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _rows(dsn: str, query: str) -> list[tuple]:
    with create_engine(dsn).connect() as conn:
        return [tuple(row) for row in conn.execute(text(query))]


def test_upgrade_crea_estensione_tabelle_e_rls(test_databases: DatabaseHandles) -> None:
    rows = _rows(
        test_databases.migration,
        "SELECT extversion FROM pg_extension WHERE extname = 'vector'",
    )
    assert len(rows) == 1

    tabelle = {
        row[0]
        for row in _rows(
            test_databases.migration,
            "SELECT relname FROM pg_class "
            "WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace",
        )
    }
    assert {
        "organizations",
        "users",
        "sessions",
        "conversations",
        "messages",
        "message_appends",
        "profiles",
        "model_bindings",
        "profile_versions",
    } <= tabelle

    rls = _rows(
        test_databases.migration,
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relname IN ('organizations', 'users', 'sessions', 'conversations', "
        "'messages', 'message_appends', "
        "'profiles', 'model_bindings', 'profile_versions')",
    )
    assert {row[0] for row in rls} == {
        "organizations",
        "users",
        "sessions",
        "conversations",
        "messages",
        "message_appends",
        "profiles",
        "model_bindings",
        "profile_versions",
    }
    assert all(row[1] is True and row[2] is True for row in rls)

    policies = {
        row[0]
        for row in _rows(
            test_databases.migration,
            "SELECT policyname FROM pg_policies "
            "WHERE tablename IN ('organizations', 'users', 'sessions', 'conversations', "
            "'messages', "
            "'message_appends', "
            "'profiles', 'model_bindings', 'profile_versions')",
        )
    }
    assert {
        "users_isolation",
        "organizations_scope",
        "sessions_select",
        "sessions_insert",
        "sessions_update",
        "sessions_delete",
        "conversations_isolation",
        "messages_isolation",
        "message_appends_isolation",
        "profiles_isolation",
        "model_bindings_isolation",
        "profile_versions_isolation",
    } <= policies

    # Vincoli che fanno da prima barriera (schema) oltre alla RLS.
    constraints = {
        row[0]
        for row in _rows(
            test_databases.migration,
            "SELECT conname FROM pg_constraint WHERE conname IN ("
            "'messages_conversation_owner', 'messages_conversation_sequence', "
            "'message_appends_scope_key', 'conversations_next_sequence_positive', "
            "'conversations_id_organization', 'conversations_id_owner', "
            "'profiles_owner_kind', 'profile_versions_profile_owner', "
            "'profile_versions_profile_version')",
        )
    }
    assert {
        "messages_conversation_owner",
        "messages_conversation_sequence",
        "message_appends_scope_key",
        "conversations_next_sequence_positive",
        "conversations_id_organization",
        "conversations_id_owner",
        "profiles_owner_kind",
        "profile_versions_profile_owner",
        "profile_versions_profile_version",
    } <= constraints


def test_upgrade_0003_popolata_preserva_sequenze_e_alloca_la_successiva(
    test_databases: DatabaseHandles,
) -> None:
    """0004 deve migrare conversazioni esistenti, non solo database vuoti.

    Popola lo schema storico 0003 con fixture SQL nel formato di quell'epoca
    — senza ``credential_hash`` (introdotto da 0005) e senza gli adapter
    correnti, che dipendono dallo schema head — così il backfill è verificato
    su dati realmente esistenti, non sul percorso applicativo corrente.
    """
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", test_databases.migration)
    command.downgrade(cfg, "0003")

    organization_id = new_id()
    user_id = new_id()
    session_id = new_id()
    conversation_id = new_id()
    created_at = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)

    engine = create_db_engine(test_databases.app)
    try:
        # Il contesto RLS è locale alla transazione (set_config ... true):
        # un solo blocco copre organizzazione, owner, sessione, conversazione
        # e messaggi, tutti nel formato storico 0003.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO organizations (id, display_name, created_at) "
                    "VALUES (:id, :name, :created)"
                ),
                {"id": organization_id, "name": "NewRay", "created": created_at},
            )
            conn.execute(
                text("SELECT set_config('app.user_id', :v, true)"),
                {"v": str(user_id)},
            )
            conn.execute(
                text("SELECT set_config('app.organization_id', :v, true)"),
                {"v": str(organization_id)},
            )
            conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, organization_id, display_name, role, created_at) "
                    "VALUES (:id, :org, :name, 'owner', :created)"
                ),
                {"id": user_id, "org": organization_id, "name": "Ada", "created": created_at},
            )
            conn.execute(
                text(
                    "INSERT INTO sessions "
                    "(id, user_id, organization_id, created_at, expires_at, revoked) "
                    "VALUES (:id, :user, :org, :created, :expires, FALSE)"
                ),
                {
                    "id": session_id,
                    "user": user_id,
                    "org": organization_id,
                    "created": created_at,
                    "expires": created_at + timedelta(days=30),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO conversations "
                    "(id, organization_id, owner_id, title, created_at, updated_at) "
                    "VALUES (:id, :org, :owner, :title, :created, :updated)"
                ),
                {
                    "id": conversation_id,
                    "org": organization_id,
                    "owner": user_id,
                    "title": "Storica",
                    "created": created_at,
                    "updated": created_at,
                },
            )
            for sequence, content in ((1, "uno"), (2, "due")):
                conn.execute(
                    text(
                        "INSERT INTO messages "
                        "(id, conversation_id, organization_id, owner_id, role, content, "
                        "sequence, created_at) "
                        "VALUES (:id, :conversation_id, :organization_id, :owner_id, "
                        "'user', :content, :sequence, :created_at)"
                    ),
                    {
                        "id": new_id(),
                        "conversation_id": conversation_id,
                        "organization_id": organization_id,
                        "owner_id": user_id,
                        "content": content,
                        "sequence": sequence,
                        "created_at": created_at,
                    },
                )
    finally:
        engine.dispose()

    # Principal sintetico coerente con la fixture: il test non risolve la
    # sessione (il percorso applicativo non è l'oggetto della verifica).
    owner = Principal(
        user_id=user_id,
        organization_id=organization_id,
        session_id=session_id,
        role=Role.OWNER,
    )

    command.upgrade(cfg, "head")
    upgraded_engine = create_db_engine(test_databases.app)
    try:
        upgraded = ConversationService(
            PostgresConversationRepository(upgraded_engine),
            PostgresMessageStore(upgraded_engine),
            SystemClock(),
        )
        assert upgraded.add_user_message(owner, conversation_id, "tre").sequence == 3
        with upgraded_engine.connect() as conn:
            with conn.begin():
                conn.execute(
                    text("SELECT set_config('app.user_id', :v, true)"),
                    {"v": str(user_id)},
                )
                conn.execute(
                    text("SELECT set_config('app.organization_id', :v, true)"),
                    {"v": str(organization_id)},
                )
                row = conn.execute(
                    text("SELECT next_sequence FROM conversations WHERE id = :id"),
                    {"id": conversation_id},
                ).one()
        assert row.next_sequence == 4
    finally:
        upgraded_engine.dispose()


def test_upgrade_0006_normalizza_nomi_owner_legacy(
    test_databases: DatabaseHandles,
) -> None:
    """0006: gli owner esistenti con margini di whitespace restano raggiungibili.

    Popola lo schema 0005 con un owner legacy il cui nome ha margini di
    whitespace (bootstrap pre-B-03.2-30) e verifica che la migrazione
    applichi la stessa regola dei casi d'uso: margini rimossi, spazi
    interni e case preservati, ID e credenziale intatti. Il login
    normalizzato raggiunge lo stesso record: nessun owner orfano.
    """
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", test_databases.migration)
    command.downgrade(cfg, "0005")

    organization_id = new_id()
    user_id = new_id()
    created_at = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    # Hash in formato canonico con iterazioni basse: il test non verifica
    # la crittografia, solo che il record passi intatto attraverso 0006.
    credential_hash = "pbkdf2_sha256$1000$c2FsdA$c2FsdA"

    engine = create_db_engine(test_databases.app)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO organizations (id, display_name, created_at) "
                    "VALUES (:id, :name, :created)"
                ),
                {"id": organization_id, "name": "NewRay", "created": created_at},
            )
            conn.execute(
                text("SELECT set_config('app.user_id', :v, true)"),
                {"v": str(user_id)},
            )
            conn.execute(
                text("SELECT set_config('app.organization_id', :v, true)"),
                {"v": str(organization_id)},
            )
            conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, organization_id, display_name, role, created_at, credential_hash) "
                    "VALUES (:id, :org, :name, 'owner', :created, :hash)"
                ),
                {
                    "id": user_id,
                    "org": organization_id,
                    "name": "  Ada Lovelace ",
                    "created": created_at,
                    "hash": credential_hash,
                },
            )
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")

    # Readback con il ruolo applicativo e il contesto di scope: la RLS è
    # FORCED anche sul proprietario delle tabelle, quindi la migrazione
    # (``newray_migrate``) non può leggere ``users`` senza contesto.
    upgraded_engine = create_db_engine(test_databases.app)
    try:
        with upgraded_engine.connect() as conn:
            with conn.begin():
                conn.execute(
                    text("SELECT set_config('app.user_id', :v, true)"),
                    {"v": str(user_id)},
                )
                conn.execute(
                    text("SELECT set_config('app.organization_id', :v, true)"),
                    {"v": str(organization_id)},
                )
                row = conn.execute(
                    text(
                        "SELECT id, display_name, credential_hash, created_at "
                        "FROM users WHERE id = :id"
                    ),
                    {"id": user_id},
                ).one()
        (row_id, row_name, row_hash, row_created) = tuple(row)
        assert row_id == user_id  # l'identificativo non è cambiato
        assert row_name == "Ada Lovelace"  # margini rimossi, spazi interni intatti
        assert row_hash == credential_hash  # la credenziale è intatta
        assert row_created == created_at  # nessun altro campo è toccato

        # Il lookup normalizzato raggiunge lo stesso record: nessun owner
        # orfano (lo stesso percorso che usa ``login``).
        user = PostgresUserRepository(upgraded_engine).find_owner_by_display_name("Ada Lovelace")
        assert user is not None
        assert user.id == user_id
    finally:
        upgraded_engine.dispose()


def test_ruoli_separati_e_grant_minimi(test_databases: DatabaseHandles) -> None:
    # Il ruolo applicativo è quello in uso e non ha privilegi di bypass:
    # è la precondizione perché RLS sia una barriera effettiva.
    app_role = _rows(
        test_databases.app,
        "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user",
    )
    assert app_role == [("newray_app", False, False)]

    # DML sì, CREATE sullo schema no.
    dml = _rows(
        test_databases.app,
        "SELECT has_table_privilege('newray_app', 'users', 'SELECT') "
        "AND has_table_privilege('newray_app', 'users', 'INSERT') "
        "AND has_table_privilege('newray_app', 'users', 'UPDATE') "
        "AND has_table_privilege('newray_app', 'users', 'DELETE') "
        "AND has_table_privilege('newray_app', 'sessions', 'SELECT') "
        "AND has_table_privilege('newray_app', 'sessions', 'INSERT') "
        "AND has_table_privilege('newray_app', 'sessions', 'UPDATE') "
        "AND has_table_privilege('newray_app', 'sessions', 'DELETE') "
        "AND has_table_privilege('newray_app', 'conversations', 'SELECT') "
        "AND has_table_privilege('newray_app', 'conversations', 'INSERT') "
        "AND has_table_privilege('newray_app', 'conversations', 'UPDATE') "
        "AND has_table_privilege('newray_app', 'conversations', 'DELETE') "
        "AND has_table_privilege('newray_app', 'messages', 'SELECT') "
        "AND has_table_privilege('newray_app', 'messages', 'INSERT') "
        "AND has_table_privilege('newray_app', 'messages', 'UPDATE') "
        "AND has_table_privilege('newray_app', 'messages', 'DELETE')",
    )
    assert dml == [(True,)]

    appends_dml = _rows(
        test_databases.app,
        "SELECT has_table_privilege('newray_app', 'message_appends', 'SELECT') "
        "AND has_table_privilege('newray_app', 'message_appends', 'INSERT') "
        "AND has_table_privilege('newray_app', 'message_appends', 'UPDATE') "
        "AND has_table_privilege('newray_app', 'message_appends', 'DELETE')",
    )
    assert appends_dml == [(True,)]

    # Profili, binding e versioni: il ruolo applicativo legge e crea
    # (seeding), ma non aggiorna né cancella — l'immutabilità vale anche
    # a livello di privilegio (NewRay.md §7.2; B-02).
    immutable = _rows(
        test_databases.app,
        "SELECT has_table_privilege('newray_app', 'profiles', 'SELECT') "
        "AND has_table_privilege('newray_app', 'profiles', 'INSERT') "
        "AND NOT has_table_privilege('newray_app', 'profiles', 'UPDATE') "
        "AND NOT has_table_privilege('newray_app', 'profiles', 'DELETE') "
        "AND has_table_privilege('newray_app', 'model_bindings', 'SELECT') "
        "AND has_table_privilege('newray_app', 'model_bindings', 'INSERT') "
        "AND NOT has_table_privilege('newray_app', 'model_bindings', 'UPDATE') "
        "AND NOT has_table_privilege('newray_app', 'model_bindings', 'DELETE') "
        "AND has_table_privilege('newray_app', 'profile_versions', 'SELECT') "
        "AND has_table_privilege('newray_app', 'profile_versions', 'INSERT') "
        "AND NOT has_table_privilege('newray_app', 'profile_versions', 'UPDATE') "
        "AND NOT has_table_privilege('newray_app', 'profile_versions', 'DELETE')",
    )
    assert immutable == [(True,)]

    create = _rows(
        test_databases.app, "SELECT has_schema_privilege('newray_app', 'public', 'CREATE')"
    )
    assert create == [(False,)]


def _public_tables(dsn: str) -> set[str]:
    return {
        row[0]
        for row in _rows(
            dsn,
            "SELECT relname FROM pg_class "
            "WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace",
        )
    }


def test_downgrade_rimuove_le_tabelle(
    test_databases: DatabaseHandles,
    sentinel_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # R01: la shell di avvio esporta NEWRAY_MIGRATION_DATABASE_URL sul DB
    # operativo. Qui la reimponiamo apposta su un DB sentinella temporaneo:
    # il downgrade configurato sul DB sacrificiale non deve poterla seguire.
    # La sentinella non è né sacrificiale né operativa: emula un bersaglio
    # estraneo che la guardia rifiuterebbe se il DSN esplicito mancasse.
    monkeypatch.setenv("NEWRAY_MIGRATION_DATABASE_URL", sentinel_database)

    # Confronto anche sui dati, non solo sui nomi delle tabelle: se una
    # regressione facesse svuotare la sentinella lasciando lo schema in
    # piedi, il solo confronto dei nomi non se ne accorgerebbe.
    sentinella_prima_tabelle = _public_tables(sentinel_database)
    sentinella_prima_versione = _rows(
        sentinel_database,
        "SELECT version_num FROM alembic_version",
    )

    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", test_databases.migration)
    command.downgrade(cfg, "base")

    # Il DB sacrificiale è stato svuotato...
    tabelle = _public_tables(test_databases.migration)
    assert not (
        {
            "organizations",
            "users",
            "sessions",
            "conversations",
            "messages",
            "profiles",
            "model_bindings",
            "profile_versions",
        }
        & tabelle
    )

    # ...e la sentinella non è stata toccata (né schema né alembic_version).
    assert _public_tables(sentinel_database) == sentinella_prima_tabelle
    assert (
        _rows(sentinel_database, "SELECT version_num FROM alembic_version")
        == sentinella_prima_versione
    )
    assert "alembic_version" in sentinella_prima_tabelle
