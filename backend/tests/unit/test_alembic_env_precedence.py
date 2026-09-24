"""Precedenza del DSN nelle migrazioni Alembic (R01).

Senza connettersi a database reali (modo offline, ``sql=True``), verifica
che il DSN effettivamente usato dalle migrazioni sia:

- il ``sqlalchemy.url`` iniettato nella configurazione, se presente:
  deve prevalere su ``NEWRAY_MIGRATION_DATABASE_URL`` (che la shell di
  avvio dei test esporta, puntando al database operativo);
- la variabile d'ambiente, solo come fallback con configurazione vuota;
- un errore esplicito, se nessuno dei due è definito.

Copro anche il contratto puro in ``migrations/urls.py`` (condiviso con
``env.py``), senza connessione.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
from types import ModuleType

import pytest
from alembic import command
from alembic.config import Config

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

ENV_URL = "postgresql+psycopg://env:env@env-host.invalid:5432/envdb"
INJECTED_URL = "sqlite:///:memory:"
FALLBACK_URL = "sqlite:///:memory:"


@pytest.fixture()
def migration_urls() -> ModuleType:
    """Carica ``migrations/urls.py`` per file (non è un package)."""
    path = os.path.join(BACKEND_DIR, "migrations", "urls.py")
    spec = importlib.util.spec_from_file_location("_newray_migration_urls_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def captured_url(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Intercetta ``MigrationContext.configure``: cattura il DSN effettivo.

    Il modo offline non crea connettori: l'intercettazione osserva solo la
    scelta del DSN, senza toccare database reali.
    """
    import alembic.runtime.migration as migration_mod

    captured: dict[str, object] = {}
    original = migration_mod.MigrationContext.configure

    def spy(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        migration_mod.MigrationContext,
        "configure",
        classmethod(lambda cls, *args, **kwargs: spy(*args, **kwargs)),
    )
    return captured


def _run_offline(cfg: Config) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        command.upgrade(cfg, "head", sql=True)


def test_url_iniettato_prevale_sull_ambiente(
    captured_url: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEWRAY_MIGRATION_DATABASE_URL", ENV_URL)

    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", INJECTED_URL)
    _run_offline(cfg)

    assert captured_url["url"] == INJECTED_URL


def test_ambiente_usato_se_configurazione_vuota(
    captured_url: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEWRAY_MIGRATION_DATABASE_URL", FALLBACK_URL)

    # alembic.ini ha ``sqlalchemy.url`` vuoto: resta il fallback CLI.
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    _run_offline(cfg)

    assert captured_url["url"] == FALLBACK_URL


def test_errore_chiaro_senza_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEWRAY_MIGRATION_DATABASE_URL", raising=False)

    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    with pytest.raises(RuntimeError, match="Nessun DSN"):
        _run_offline(cfg)


# --- Contratto puro condiviso (migrations/urls.py) -------------------------


def test_resolve_preferisce_il_dsn_iniettato(
    migration_urls: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEWRAY_MIGRATION_DATABASE_URL", ENV_URL)
    assert migration_urls.resolve_database_url(INJECTED_URL) == INJECTED_URL


def test_resolve_fallback_all_ambiente(
    migration_urls: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEWRAY_MIGRATION_DATABASE_URL", ENV_URL)
    assert migration_urls.resolve_database_url(None) == ENV_URL


def test_resolve_senza_sorgenti_ritorna_none(
    migration_urls: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEWRAY_MIGRATION_DATABASE_URL", raising=False)
    assert migration_urls.resolve_database_url(None) is None


def test_is_disposable_database(migration_urls: ModuleType) -> None:
    assert migration_urls.is_disposable_database("postgresql+psycopg://u:p@h:5433/newray_it_abc123")
    assert not migration_urls.is_disposable_database("postgresql+psycopg://u:p@h:5433/newray")
