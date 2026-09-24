"""Risoluzione e guardia del DSN di migrazione (R01, B-03.2-01).

Precedenza esplicita: il DSN iniettato esplicitamente (test, ``alembic.ini``)
vale sulla variabile operativa ``NEWRAY_MIGRATION_DATABASE_URL``. Una shell che
esporta la variabile operativa non può mai ridirigere un test sul database
operativo. Le migrazioni toccano solo database di test monouso
(prefisso ``newray_it_``); ogni altro bersaglio viene rifiutato prima di
upgrade/downgrade. Il DSN non viene mai stampato.
"""

from __future__ import annotations

import os

from sqlalchemy.engine.url import make_url

#: Variabile operativa del ruolo proprietario delle migrazioni.
OPERATIONAL_ENV_VAR = "NEWRAY_MIGRATION_DATABASE_URL"

#: Prefisso dei database di test monouso creati dalle fixture di integrazione.
DISPOSABLE_DB_PREFIX = "newray_it_"


def resolve_database_url(injected: str | None) -> str | None:
    """Precedenza: DSN iniettato > variabile operativa > ``None``."""
    if injected:
        return injected
    return os.environ.get(OPERATIONAL_ENV_VAR)


def database_name(dsn: str) -> str:
    """Nome database di un DSN (senza query string né credenziali).

    Usa il parser URL di SQLAlchemy: credenziali, query string e driver
    non influenzano l'estrazione del nome (B-03.2-01).
    """
    return make_url(dsn).database or ""


def is_disposable_database(dsn: str) -> bool:
    """True se il DSN punta a un database di test monouso."""
    return database_name(dsn).startswith(DISPOSABLE_DB_PREFIX)
