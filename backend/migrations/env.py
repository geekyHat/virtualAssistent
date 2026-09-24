"""Ambiente Alembic: migrazioni eseguite dal ruolo proprietario (ADR 0002).

Precedenza del DSN (logica condivisa con i test in ``urls.py``, verificata
senza connessione):

1. ``sqlalchemy.url`` esplicito nella configurazione Alembic (``alembic.ini``
   o iniettato programmaticamente, p. es. dalle fixture di integrazione);
2. ``NEWRAY_MIGRATION_DATABASE_URL`` (ruolo ``newray_migrate``, proprietario
   delle tabelle), solo come fallback per l'uso da riga di comando.

La variabile d'ambiente non deve mai dirottare un comando che ha già un
bersaglio esplicito: una shell che la esporta (come chiedono le istruzioni
di avvio dei test) non può fare operare upgrade/downgrade su un database
diverso da quello configurato (R01).

Il ruolo applicativo non migra mai lo schema: per lui le migrazioni sono già
applicate. Le migrazioni sono scritte a mano: ``target_metadata`` è None e
l'autogenerate non è in uso.
"""

from __future__ import annotations

import importlib.util
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool


def _load_sibling_module(module_name: str, filename: str) -> importlib.util.module:
    """Carica un modulo fianco a ``env.py`` senza dipendere da ``sys.path``.

    ``env.py`` è uno script eseguito da Alembic (CLI o API) con percorsi
    arbitrari: un ``import`` normale di ``urls`` fallirebbe quando la
    directory delle migrazioni non è nel path del processo.
    """
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).with_name(filename))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration_urls = _load_sibling_module("newray_migration_urls", "urls.py")
OPERATIONAL_ENV_VAR = migration_urls.OPERATIONAL_ENV_VAR
resolve_database_url = migration_urls.resolve_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_url() -> str:
    """Risoluzione del DSN: configurazione esplicita prima dell'ambiente."""
    explicit = (config.get_main_option("sqlalchemy.url") or "").strip() or None
    resolved = resolve_database_url(explicit)
    if not resolved:
        raise RuntimeError(
            "Nessun DSN per le migrazioni: imposta sqlalchemy.url in alembic.ini "
            f"o la variabile {OPERATIONAL_ENV_VAR}."
        )
    return resolved


target_metadata = None


def run_migrations_offline() -> None:
    """Migrazioni offline: emette SQL senza connessione."""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Migrazioni online: transazione singola per revisione."""
    config.set_main_option("sqlalchemy.url", _resolve_url().replace("%", "%%"))
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
