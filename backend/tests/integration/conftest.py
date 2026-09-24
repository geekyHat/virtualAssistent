"""Fixtures di integrazione su PostgreSQL reale (NewRay.md §§7.3, 22.2).

Tre DSN via ambiente, a simulare i ruoli reali (ADR 0002):

- ``NEWRAY_TEST_ADMIN_URL``: superuser (crea i database di test);
- ``NEWRAY_TEST_MIGRATION_URL``: ``newray_migrate`` (proprietario
  migrazioni, owner delle tabelle);
- ``NEWRAY_TEST_DATABASE_URL``: ``newray_app`` (senza superuser né
  BYPASSRLS): è il ruolo con cui gli adapter operano.

Senza configurazione i test si saltano: la suite ordinaria resta
offline. Non sostituiscono questi test i fake (NewRay.md §22.2).
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.engine import Engine

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _load_migration_urls() -> importlib.util.module:
    """Carica ``migrations/urls.py`` (logica DSN condivisa con env.py).

    La directory delle migrazioni non è un package: alembic la mette in
    ``sys.path`` solo quando esegue ``env.py``. Qui la si carica esplicita-
    mente per file, senza toccare ``sys.path``.
    """
    path = os.path.join(BACKEND_DIR, "migrations", "urls.py")
    spec = importlib.util.spec_from_file_location("_newray_migration_urls", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration_urls = _load_migration_urls()


@dataclass(frozen=True)
class DatabaseHandles:
    """DSN di un database di test fresco (schema migrato a head)."""

    app: str  # ruolo applicativo (senza privilegi di bypass)
    migration: str  # ruolo proprietario delle migrazioni
    admin: str  # superuser
    name: str


def _require_env() -> tuple[str, str, str]:
    admin = os.environ.get("NEWRAY_TEST_ADMIN_URL")
    migration = os.environ.get("NEWRAY_TEST_MIGRATION_URL")
    app = os.environ.get("NEWRAY_TEST_DATABASE_URL")
    if not (admin and migration and app):
        pytest.skip(
            "NEWRAY_TEST_ADMIN_URL / NEWRAY_TEST_MIGRATION_URL / "
            "NEWRAY_TEST_DATABASE_URL non definiti"
        )
    return admin, migration, app


def _with_database(dsn: str, dbname: str) -> str:
    """Sostituisce il nome del database in un DSN.

    Parser URL di SQLAlchemy, mai concatenazione di stringhe: credenziali
    e query string restano intatte (B-03.2-01).
    """
    return make_url(dsn).set(database=dbname).render_as_string(hide_password=False)


def _assert_sacrificial_target(
    dsn: str, dbname: str, admin_engine: Engine, expected_owner: str
) -> None:
    """Verifica che il bersaglio delle migrazioni sia il DB sacrificiale atteso.

    Prima di upgrade/downgrade, garantisce (R01) che il DSN punti a un
    database di test con il prefisso sacrificiale, che sia proprio quello
    appena creato e che il proprietario sia il ruolo atteso — mai un
    database operativo ereditato dall'ambiente. I messaggi non includono
    il DSN completo (no credenziali nei log).
    """
    url = make_url(dsn)
    if not url.database:
        msg = f"DSN di migrazione senza database: {dsn.split('@')[-1]}"
        raise AssertionError(msg)
    if url.database != dbname:
        msg = f"DSN di migrazione ({url.database}) != database atteso ({dbname})"
        raise AssertionError(msg)
    if not migration_urls.is_disposable_database(dsn):
        msg = (
            f"Database non sacrificiale ({dbname}): le migrazioni dei test "
            f"possono colpire solo database con prefisso "
            f"{migration_urls.DISPOSABLE_DB_PREFIX!r}"
        )
        raise AssertionError(msg)
    with admin_engine.connect() as conn:
        owners = [
            row[0]
            for row in conn.execute(
                text("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = :name"),
                {"name": dbname},
            )
        ]
    if owners != [expected_owner]:
        msg = f"Proprietario di {dbname} inatteso: {owners!r} != {expected_owner!r}"
        raise AssertionError(msg)


@pytest.fixture()
def test_databases(monkeypatch: pytest.MonkeyPatch) -> Iterator[DatabaseHandles]:
    """Un database fresco per test: migrato a head, rimosso a fine test.

    Isola la variabile ``NEWRAY_MIGRATION_DATABASE_URL``: la shell di avvio
    la esporta, ma le migrazioni delle fixture devono colpire solo il DB
    sacrificiale iniettato in configurazione, mai quello dell'ambiente (R01).
    """
    admin, migration, app = _require_env()
    monkeypatch.delenv("NEWRAY_MIGRATION_DATABASE_URL", raising=False)
    dbname = f"newray_it_{uuid.uuid4().hex[:10]}"

    # CREATE/DROP DATABASE e CREATE EXTENSION non corrono in transazione
    # (o verrebbero rollati a chiusura): il superuser opera in autocommit.
    admin_engine = create_engine(admin, execution_options={"isolation_level": "AUTOCOMMIT"})
    try:
        fresh_engine: Engine | None = None
        try:
            with admin_engine.connect() as conn:
                conn.exec_driver_sql(f'CREATE DATABASE "{dbname}" OWNER newray_migrate')

            fresh_admin = _with_database(admin, dbname)
            fresh_engine = create_engine(
                fresh_admin, execution_options={"isolation_level": "AUTOCOMMIT"}
            )
            with fresh_engine.connect() as conn:
                # Estensione di livello database: nel flusso di produzione la crea
                # il bootstrap (bootstrap.sql); qui la crea il superuser di test.
                conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
            fresh_engine.dispose()
            fresh_engine = None

            fresh_migration = _with_database(migration, dbname)
            fresh_app = _with_database(app, dbname)

            # Verifica del bersaglio prima di toccare lo schema (R01).
            _assert_sacrificial_target(fresh_migration, dbname, admin_engine, "newray_migrate")

            cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
            cfg.set_main_option("sqlalchemy.url", fresh_migration)
            command.upgrade(cfg, "head")

            yield DatabaseHandles(
                app=fresh_app, migration=fresh_migration, admin=fresh_admin, name=dbname
            )
        finally:
            if fresh_engine is not None:
                # Setup fallito a metà: dispone il pool ancora aperto.
                fresh_engine.dispose()
    finally:
        # Il DROP copre anche gli errori di setup: nessun residuo sul
        # cluster, solo il DB creato da questa fixture (R01).
        with admin_engine.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        admin_engine.dispose()


#: Prefisso dei database sentinella. Non coincide con ``DISPOSABLE_DB_PREFIX``:
#: la sentinella rappresenta, per i test di downgrade, un bersaglio che la
#: guardia sacrificiale rifiuterebbe se venisse puntato per errore.
SENTINEL_DB_PREFIX = "newray_sentinel_"


@pytest.fixture()
def sentinel_database() -> Iterator[str]:
    """Database sentinella temporaneo, migrato a head, distrutto a fine test.

    Serve ai test di downgrade per dimostrare che un comando puntato su un
    DB sacrificiale non tocca un secondo database, anche se l'ambiente
    espone ``NEWRAY_MIGRATION_DATABASE_URL`` (R01). Non usare mai il
    database operativo ``newray``: un cluster locale può contenerlo con
    dati reali e la fixture non deve modificarlo.

    Il nome del bersaglio è generato per test (UUID) con un prefisso
    distinto da ``DISPOSABLE_DB_PREFIX``: la sentinella emula «un DB
    estraneo» dal punto di vista della guardia sacrificiale, esattamente
    la condizione che il test di downgrade verifica.
    """
    admin, migration, _ = _require_env()
    dbname = f"{SENTINEL_DB_PREFIX}{uuid.uuid4().hex[:10]}"

    admin_engine = create_engine(admin, execution_options={"isolation_level": "AUTOCOMMIT"})
    created = False
    try:
        with admin_engine.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{dbname}" OWNER newray_migrate')
        created = True

        fresh_admin_dsn = _with_database(admin, dbname)
        fresh_engine = create_engine(
            fresh_admin_dsn, execution_options={"isolation_level": "AUTOCOMMIT"}
        )
        try:
            with fresh_engine.connect() as conn:
                conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        finally:
            fresh_engine.dispose()

        sentinel_dsn = _with_database(migration, dbname)
        # La sentinella non è sacrificiale (prefisso diverso): la guardia
        # ``_assert_sacrificial_target`` la rifiuterebbe. È una verifica in
        # più che il DROP finale colpisca solo il DB creato qui — evita che
        # una regressione della guardia porti a distruggere un DB estraneo.
        assert migration_urls.database_name(sentinel_dsn) == dbname
        assert not migration_urls.is_disposable_database(sentinel_dsn)

        cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", sentinel_dsn)
        command.upgrade(cfg, "head")

        yield sentinel_dsn
    finally:
        if created:
            with admin_engine.connect() as conn:
                conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        admin_engine.dispose()
