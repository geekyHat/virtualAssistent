"""Risoluzione del Principal a livello HTTP (NewRay.md §§7.1, 20.3).

L'unico identificatore accettato è il cookie di sessione opaco emesso dal
server. Header e parametri del client (``X-User-Id``, ``X-Role``, ecc.) non
vengono mai letti: gli identificativi del browser non sono fidati.

Gli attributi del cookie (HttpOnly, SameSite, Secure) sono impostati dalla
route che emette il cookie (A-04); qui si gestisce solo la lettura.
"""

from __future__ import annotations

import uuid

from starlette.requests import Request

from newray.kernel.errors import DomainError
from newray.kernel.identity import Principal
from newray.modules.identity import IdentityService

#: Nome del cookie di sessione; valore opaco, HttpOnly a emissione.
SESSION_COOKIE = "newray_session"


class Unauthenticated(DomainError):
    """Richiesta senza sessione valida: nessun principal risolto."""

    code = "UNAUTHENTICATED"


def resolve_principal_from_request(request: Request, service: IdentityService) -> Principal:
    """Dipendenza del trasporto: principal dal server, mai dal client.

    ``Unauthenticated`` e ``SessionInvalid`` vengono mappati su 401 dal
    modulo errori (``interfaces/http/errors.py``).
    """
    raw = request.cookies.get(SESSION_COOKIE)
    if raw is None:
        raise Unauthenticated("mancante cookie di sessione")
    try:
        session_id = uuid.UUID(raw)
    except ValueError as exc:
        raise Unauthenticated("cookie di sessione non valido") from exc
    return service.resolve_principal(session_id)


def get_identity_service(request: Request) -> IdentityService:
    """Il servizio di identità è impostato dal bootstrap (``app.state``):
    il trasporto non compone mai i casi d'uso da solo."""
    service = request.app.state.identity_service
    assert isinstance(service, IdentityService)
    return service


def require_principal(request: Request) -> Principal:
    """Dipendenza FastAPI per le route protette: principal risolto dal
    server; senza cookie valido la richiesta non prosegue."""
    return resolve_principal_from_request(request, get_identity_service(request))
