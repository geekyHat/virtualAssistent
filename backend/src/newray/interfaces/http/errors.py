"""Mapping errori di dominio → HTTP (NewRay.md §19.4).

Codici stabili che non dipendono dalla localizzazione del messaggio;
``retryable`` dice se un nuovo tentativo con lo stesso payload ha senso
(i retry ciechi sono vietati per gli esiti incerti, §8.3). Ogni payload
porta un correlation ID (header ``X-Request-ID`` in ingresso se valido,
altrimenti generato) e non contiene segreti, dettagli interni o stack trace.
"""

from __future__ import annotations

import re
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

from newray.kernel.errors import DomainError

#: Stato HTTP per codice di dominio; il default per codici non previsti
#: è 400 (errore del client, mai un fallimento nascosto).
_STATUS_BY_CODE: dict[str, int] = {
    "UNAUTHENTICATED": 401,
    "SESSION_INVALID": 401,
    "INVALID_CREDENTIALS": 401,
    "INVALID_NAME": 400,
    "CREDENTIAL_NOT_SET": 409,
    "ACCESS_DENIED": 403,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "MODEL_UNAVAILABLE": 503,
    "INFERENCE_FAILED": 502,
    "INFERENCE_TIMEOUT": 504,
}

#: Codici per cui un nuovo tentativo identico può avere esito diverso.
_RETRYABLE_CODES: frozenset[str] = frozenset({"INFERENCE_TIMEOUT"})

_INTERNAL_CODE = "INTERNAL"
_VALIDATION_CODE = "VALIDATION_ERROR"

_MAX_REQUEST_ID_LENGTH = 128
_REQUEST_ID_PATTERN = re.compile(r"^[\x20-\x7E]+$")


def correlation_id(request: Request) -> str:
    """ID di correlazione: X-Request-ID del client se valido, altrimenti
    generato dal server. Validazione: solo ASCII stampabile, max 128 caratteri,
    nessun carattere di controllo (B-03.2-15)."""
    raw = request.headers.get("X-Request-ID")
    if raw and len(raw) <= _MAX_REQUEST_ID_LENGTH and _REQUEST_ID_PATTERN.match(raw):
        return raw
    return str(uuid.uuid4())


def error_body(
    request: Request, code: str, message: str, retryable: bool | None = None
) -> dict[str, object]:
    """Payload di errore pubblico: struttura fissa, nessun dettaglio interno."""
    return {
        "code": code,
        "message": message,
        "retryable": retryable if retryable is not None else code in _RETRYABLE_CODES,
        "correlation_id": correlation_id(request),
    }


def install_error_handlers(app: FastAPI) -> None:
    """Aggiunge i handler degli errori di dominio, validazione e fallimenti."""

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        status = _STATUS_BY_CODE.get(exc.code, 400)
        return JSONResponse(status_code=status, content=error_body(request, exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = []
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", []))
            details.append(f"{loc}: {err.get('msg', 'errore di validazione')}")
        message = "; ".join(details) if details else "dati non validi"
        return JSONResponse(
            status_code=422,
            content=error_body(request, _VALIDATION_CODE, message, retryable=False),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=error_body(
                request, _INTERNAL_CODE, "errore interno del server", retryable=False
            ),
        )
