"""Entrata di avvio dell'API (NewRay.md §21.2; A-07).

Avvio unico documentato::

    uv run python -m newray.bootstrap.main

uvicorn viene lanciato con bind e porta presi dai settings validati:
loopback di default, un bind non-loopback richiede
``NEWRAY_COOKIE_SECURE=true`` (NewRay.md §20.3). I DSN non appaiono
mai nei log.
"""

from __future__ import annotations

import uvicorn

from newray.bootstrap.api import build_app
from newray.bootstrap.settings import Settings


def main() -> None:
    """Risoluzione settings (validati) e avvio di uvicorn."""
    settings = Settings()  # type: ignore[call-arg]
    uvicorn.run(build_app(), host=settings.bind_address, port=settings.port)


if __name__ == "__main__":
    main()
