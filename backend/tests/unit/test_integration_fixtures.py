"""Sonda offline delle fixture di integrazione (B-03.2-01, R01).

La fixture ``sentinel_database`` non deve mai puntare al database
operativo ``newray``, indipendentemente da ``NEWRAY_MIGRATION_DATABASE_URL``.
Il test intercetta ``alembic.command.upgrade`` e ``sqlalchemy.create_engine``
per registrare i bersagli senza aprire connessioni reali: la suite ordinaria
resta offline (NewRay.md §22.4).
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
INTEGRATION_DIR = BACKEND_DIR / "tests" / "integration"


@pytest.fixture()
def conftest_module() -> Iterator[Any]:
    """Carica ``tests/integration/conftest.py`` come modulo isolato."""
    added_paths = [
        str(BACKEND_DIR / "migrations"),  # migration_urls carica per file
        str(INTEGRATION_DIR),  # DatabaseHandles importa "conftest"
    ]
    for p in added_paths:
        if p not in sys.path:
            sys.path.insert(0, p)
    mod_name = "_integration_conftest_probe"
    spec = importlib.util.spec_from_file_location(mod_name, INTEGRATION_DIR / "conftest.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # ``dataclass`` risolve annotazioni via ``sys.modules[cls.__module__]``:
    # il modulo deve essere registrato prima di ``exec_module``.
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(mod_name, None)
        for p in added_paths:
            if p in sys.path:
                sys.path.remove(p)


def _consume(generator: Iterator[Any]) -> Any:
    """Prende il primo yield e chiude senza rilanciare GeneratorExit."""
    value = next(generator)
    generator.close()
    return value


def test_sentinel_database_never_targets_operational_newray(
    conftest_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La fixture non deve migrare o droppare il database ``newray``.

    Regressione da B-03.2-01: la vecchia ``operational_database`` chiamava
    ``upgrade(head)`` su un DSN fisso ``…/newray``. Il fix crea un database
    sentinella con prefisso distinto (UUID) e lo elimina a fine test.
    """
    # Anche se la shell che avvia i test esporta la variabile operativa, la
    # fixture non deve mai seguirla per costruire il bersaglio delle
    # migrazioni: la simuliamo esplicitamente, per prova negativa.
    monkeypatch.setenv("NEWRAY_MIGRATION_DATABASE_URL", "postgresql+psycopg://x/newray")

    admin = "postgresql+psycopg://admin@localhost/postgres"
    migration = "postgresql+psycopg://mig@localhost/postgres"
    app = "postgresql+psycopg://app@localhost/postgres"

    upgrade_targets: list[str] = []
    exec_sql: list[str] = []

    def _fake_upgrade(cfg: Any, revision: str) -> None:
        url = cfg.get_main_option("sqlalchemy.url")
        upgrade_targets.append(conftest_module.migration_urls.database_name(url))

    def _fake_create_engine(dsn: str, **_: Any) -> MagicMock:
        engine = MagicMock()
        # Il connect() è usato come context manager: ritorna la connessione
        # che registra ogni exec_driver_sql (CREATE/DROP DATABASE, ecc.).
        conn = MagicMock()
        conn.exec_driver_sql = lambda sql, *a, **k: exec_sql.append(sql)
        engine.connect.return_value.__enter__ = lambda self: conn
        engine.connect.return_value.__exit__ = lambda self, *a: False
        return engine

    with (
        patch.object(conftest_module, "_require_env", return_value=(admin, migration, app)),
        patch.object(conftest_module.command, "upgrade", side_effect=_fake_upgrade),
        patch.object(conftest_module, "create_engine", side_effect=_fake_create_engine),
    ):
        # ``sentinel_database`` è decorata con @pytest.fixture: il generatore
        # vero è in ``__wrapped__`` (stesso pattern del rapporto di revisione).
        _consume(conftest_module.sentinel_database.__wrapped__())

    # Prova nominale: esattamente un upgrade, e su un database sentinella
    # con prefisso identificabile.
    assert len(upgrade_targets) == 1, upgrade_targets
    (target,) = upgrade_targets
    assert target.startswith(conftest_module.SENTINEL_DB_PREFIX), target
    assert re.fullmatch(rf"{conftest_module.SENTINEL_DB_PREFIX}[0-9a-f]{{10}}", target), target

    # Prova negativa forte: nessun bersaglio ``newray`` e nessun DSN
    # sacrificiale nei parametri (la sentinella emula un DB "estraneo").
    assert "newray" not in upgrade_targets
    assert not conftest_module.migration_urls.is_disposable_database(f"postgresql://x/{target}")

    # I comandi driver (CREATE/DROP DATABASE, CREATE EXTENSION) devono
    # citare solo il DB sentinella, mai il DB operativo. Verifica letterale.
    joined = "\n".join(exec_sql)
    assert 'CREATE DATABASE "' + target + '"' in joined
    assert 'DROP DATABASE IF EXISTS "' + target + '"' in joined
    # Nessun comando cita il DB operativo. Ricerca robusta a spazi/virgolette.
    assert not re.search(r'\bnewray"?\s*(OWNER|WITH|;|$)', joined), joined


def test_sentinel_database_drops_on_setup_failure(conftest_module: Any) -> None:
    """Se l'upgrade fallisce, il DB creato deve essere rimosso comunque.

    Il teardown è in ``try/finally``: un'eccezione durante Alembic non
    lascia residui sul cluster.
    """
    admin = "postgresql+psycopg://admin@localhost/postgres"
    migration = "postgresql+psycopg://mig@localhost/postgres"
    app = "postgresql+psycopg://app@localhost/postgres"

    exec_sql: list[str] = []

    def _fake_create_engine(dsn: str, **_: Any) -> MagicMock:
        engine = MagicMock()
        conn = MagicMock()
        conn.exec_driver_sql = lambda sql, *a, **k: exec_sql.append(sql)
        engine.connect.return_value.__enter__ = lambda self: conn
        engine.connect.return_value.__exit__ = lambda self, *a: False
        return engine

    with (
        patch.object(conftest_module, "_require_env", return_value=(admin, migration, app)),
        patch.object(conftest_module.command, "upgrade", side_effect=RuntimeError("boom")),
        patch.object(conftest_module, "create_engine", side_effect=_fake_create_engine),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            _consume(conftest_module.sentinel_database.__wrapped__())

    # Il DB creato è stato distrutto anche dopo il guasto dell'upgrade.
    creates = [s for s in exec_sql if s.startswith("CREATE DATABASE")]
    drops = [s for s in exec_sql if s.startswith("DROP DATABASE")]
    assert len(creates) == 1, creates
    assert len(drops) == 1, drops
    # Nome del DB coerente fra CREATE e DROP.
    (created_name,) = re.findall(r'CREATE DATABASE "([^"]+)"', creates[0])
    (dropped_name,) = re.findall(r'DROP DATABASE IF EXISTS "([^"]+)"', drops[0])
    assert created_name == dropped_name
    assert created_name.startswith(conftest_module.SENTINEL_DB_PREFIX)


def test_test_databases_migrates_disposable_and_drops_on_close(
    conftest_module: Any,
) -> None:
    """Nominale: upgrade solo sul DB sacrificiale, DROP alla chiusura del yield.

    Il DSN iniettato in configurazione è costruito con il parser URL di
    SQLAlchemy (password e host intatti) e il teardown copre anche gli
    errori di setup (B-03.2-01).
    """
    admin = "postgresql+psycopg://admin@localhost/postgres"
    migration = "postgresql+psycopg://mig@localhost/postgres"
    app = "postgresql+psycopg://app@localhost/postgres"

    upgrade_targets: list[str] = []
    exec_sql: list[str] = []

    def _fake_upgrade(cfg: Any, revision: str) -> None:
        upgrade_targets.append(
            conftest_module.migration_urls.database_name(cfg.get_main_option("sqlalchemy.url"))
        )

    def _fake_create_engine(dsn: str, **_: Any) -> MagicMock:
        engine = MagicMock()
        conn = MagicMock()
        conn.exec_driver_sql = lambda sql, *a, **k: exec_sql.append(sql)
        engine.connect.return_value.__enter__ = lambda self: conn
        engine.connect.return_value.__exit__ = lambda self, *a: False
        return engine

    with (
        patch.object(conftest_module, "_require_env", return_value=(admin, migration, app)),
        patch.object(conftest_module.command, "upgrade", side_effect=_fake_upgrade),
        patch.object(conftest_module, "create_engine", side_effect=_fake_create_engine),
        patch.object(conftest_module, "_assert_sacrificial_target"),
    ):
        handles = _consume(conftest_module.test_databases.__wrapped__(pytest.MonkeyPatch()))

    # L'upgrade colpisce un solo DB, sacrificiale e coerente con il nome.
    assert len(upgrade_targets) == 1, upgrade_targets
    (target,) = upgrade_targets
    assert target == handles.name
    assert conftest_module.migration_urls.is_disposable_database(f"postgresql://x/{target}")
    # DSN costruito con il parser URL: host/credenziali intatti, solo il
    # database sostituito.
    assert handles.migration == f"postgresql+psycopg://mig@localhost/{target}"
    assert handles.app == f"postgresql+psycopg://app@localhost/{target}"
    # Il teardown elimina esattamente il DB creato.
    assert f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)' in exec_sql


def test_test_databases_drops_on_setup_failure(conftest_module: Any) -> None:
    """Se l'upgrade fallisce, il DB creato deve essere rimosso comunque.

    Il teardown è in ``try/finally`` che copre il setup: un'eccezione
    durante Alembic non lascia residui sul cluster (B-03.2-01).
    """
    admin = "postgresql+psycopg://admin@localhost/postgres"
    migration = "postgresql+psycopg://mig@localhost/postgres"
    app = "postgresql+psycopg://app@localhost/postgres"

    exec_sql: list[str] = []

    def _fake_create_engine(dsn: str, **_: Any) -> MagicMock:
        engine = MagicMock()
        conn = MagicMock()
        conn.exec_driver_sql = lambda sql, *a, **k: exec_sql.append(sql)
        engine.connect.return_value.__enter__ = lambda self: conn
        engine.connect.return_value.__exit__ = lambda self, *a: False
        return engine

    with (
        patch.object(conftest_module, "_require_env", return_value=(admin, migration, app)),
        patch.object(conftest_module.command, "upgrade", side_effect=RuntimeError("boom")),
        patch.object(conftest_module, "create_engine", side_effect=_fake_create_engine),
        patch.object(conftest_module, "_assert_sacrificial_target"),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            _consume(conftest_module.test_databases.__wrapped__(pytest.MonkeyPatch()))

    creates = [s for s in exec_sql if s.startswith("CREATE DATABASE")]
    drops = [s for s in exec_sql if s.startswith("DROP DATABASE")]
    assert len(creates) == 1, creates
    assert len(drops) == 1, drops
    (created_name,) = re.findall(r'CREATE DATABASE "([^"]+)"', creates[0])
    (dropped_name,) = re.findall(r'DROP DATABASE IF EXISTS "([^"]+)"', drops[0])
    assert created_name == dropped_name
    assert created_name.startswith(conftest_module.migration_urls.DISPOSABLE_DB_PREFIX)


def test_disposable_prefix_and_sentinel_prefix_are_distinct(conftest_module: Any) -> None:
    """La sentinella non deve entrare nella whitelist sacrificiale.

    ``is_disposable_database`` autorizza solo ``newray_it_*``. La sentinella
    usa un prefisso diverso proprio per emulare, nel test di downgrade, un
    bersaglio estraneo che la guardia rifiuterebbe.
    """
    assert conftest_module.SENTINEL_DB_PREFIX != conftest_module.migration_urls.DISPOSABLE_DB_PREFIX
    sentinel_sample = f"{conftest_module.SENTINEL_DB_PREFIX}deadbeef01"
    assert not conftest_module.migration_urls.is_disposable_database(
        f"postgresql://x/{sentinel_sample}"
    )


def test_operational_database_fixture_removed(conftest_module: Any) -> None:
    """La vecchia fixture non deve più esistere (regressione di nome)."""
    assert not hasattr(conftest_module, "operational_database"), (
        "operational_database rimosso: usare sentinel_database (B-03.2-01)"
    )
    # Nessun riferimento residuo al nome fisso ``"newray"`` nel conftest.
    conftest_src = (INTEGRATION_DIR / "conftest.py").read_text()
    assert '"newray"' not in conftest_src, (
        "il conftest non deve più fare riferimento al database operativo per nome"
    )
    # Verifica supplementare: nessun test consumer usa il vecchio nome.
    for test_file in INTEGRATION_DIR.glob("test_*.py"):
        text = test_file.read_text()
        assert "operational_database" not in text, (
            f"{test_file.name} usa ancora operational_database"
        )


def _ensure_backend_dir_env() -> None:
    # Utility per far girare questo file anche via ``python -m pytest``
    # senza aver esportato variabili aggiuntive.
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


_ensure_backend_dir_env()
