"""Smoke test dello stack risolto in A-01.

Verifica il criterio di uscita A-01 lato runtime: le dipendenze dichiarate
in ``pyproject.toml`` devono essere importabili nell'ambiente prodotto dal
lockfile, con le major attese dalla specifica (NewRay.md §4.2).
"""

import sqlalchemy


def test_runtime_dipendenze_importabili() -> None:
    # Import espliciti: un fallimento di risoluzione del lockfile deve
    # emergere qui, non nel primo avvio reale del server.
    import alembic
    import fastapi
    import httpx
    import psycopg
    import pydantic
    import pydantic_settings
    import uvicorn

    assert fastapi.__version__
    assert pydantic.VERSION.startswith("2.")
    assert psycopg.__version__
    assert uvicorn is not None
    assert alembic is not None
    assert httpx is not None
    assert pydantic_settings is not None


def test_sqlalchemy_major_2() -> None:
    # La specifica richiede SQLAlchemy 2 (API tipizzata, niente ORM nei DTO).
    assert sqlalchemy.__version__.startswith("2.")
