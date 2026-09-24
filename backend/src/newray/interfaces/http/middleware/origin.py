"""Confine same-origin per la WebUI autenticata (NewRay.md §§18.4, 19.2, 20.3).

Il cookie HttpOnly non basta per autorizzare una mutazione: il browser può
allegarlo a una richiesta cross-site. Questo middleware del trasporto vincola
Host e Origin alla sola origine WebUI configurata, prima che route o casi d'uso
possano produrre effetti.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlparse

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class SameOriginMiddleware:
    """Rifiuta Host estranei e mutazioni senza Origin canonico.

    Non è CORS: l'API autenticata non ammette un'altra origine. Il controllo
    viene eseguito prima dell'applicazione, così anche bootstrap e revoca sono
    senza effetti quando Host/Origin non sono ammessi.
    """

    def __init__(self, app: ASGIApp, *, public_origin: str) -> None:
        parsed = urlparse(public_origin)
        self.app = app
        self._origin = public_origin
        self._host = parsed.netloc.lower()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        host = request.headers.get("host", "").lower()
        if host != self._host:
            await _deny(scope, receive, send, "host non ammesso")
            return
        if request.method in _UNSAFE_METHODS and request.headers.get("origin") != self._origin:
            await _deny(scope, receive, send, "origine non ammessa")
            return
        await self.app(scope, receive, send)


async def _deny(scope: Scope, receive: Receive, send: Send, message: str) -> None:
    """Risposta minima e non riflettente per richieste bloccate al confine."""
    response = JSONResponse(
        status_code=403,
        content={
            "code": "ACCESS_DENIED",
            "message": message,
            "retryable": False,
            "correlation_id": str(uuid.uuid4()),
        },
    )
    await response(scope, receive, send)
