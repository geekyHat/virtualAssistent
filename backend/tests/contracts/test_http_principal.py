"""Contratto HTTP (NewRay.md §§7.1, 20.3).

Il principal viene risolto solo dal cookie di sessione opaco; header e
parametri del client non partecipano mai alla risoluzione.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from starlette.requests import Request

from fakes import (
    FakeClock,
    InMemoryOwnerBootstrap,
    InMemorySessionStore,
    InMemoryUserRepository,
)
from newray.interfaces.http.middleware.identity import (
    SESSION_COOKIE,
    Unauthenticated,
    resolve_principal_from_request,
)
from newray.kernel.identity import Principal
from newray.modules.identity import IdentityService

EPOCA = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def _service() -> tuple[IdentityService, Principal]:
    users = InMemoryUserRepository()
    sessions = InMemorySessionStore()
    service = IdentityService(
        users, sessions, FakeClock(EPOCA), InMemoryOwnerBootstrap(users, sessions)
    )
    principal = service.bootstrap_owner("P", "test-passphrase-1234")
    return service, principal


def _request(cookie_value: str | None, extra_headers: list[tuple[str, str]]) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if cookie_value is not None:
        headers.append((b"cookie", f"{SESSION_COOKIE}={cookie_value}".encode()))
    for name, value in extra_headers:
        headers.append((name.lower().encode(), value.encode()))
    return Request({"type": "http", "headers": headers})


def test_cookie_valido_risolve_il_principal_di_server() -> None:
    service, principal = _service()
    request = _request(str(principal.session_id), [])
    assert resolve_principal_from_request(request, service) == principal


def test_header_di_identita_non_sono_fidati() -> None:
    """X-User-Id/X-Role del client non influenzano la risoluzione."""
    service, principal = _service()
    attaccante = str(uuid.uuid4())
    request = _request(
        str(principal.session_id),
        [
            ("x-user-id", attaccante),
            ("x-role", "owner"),
            ("x-organization-id", attaccante),
        ],
    )
    risolto = resolve_principal_from_request(request, service)
    assert risolto == principal
    assert risolto.user_id != uuid.UUID(attaccante)
    assert risolto.role.name == "OWNER"


def test_senza_cookie_nessun_principal() -> None:
    service, _ = _service()
    request = _request(None, [("x-user-id", str(uuid.uuid4()))])
    with pytest.raises(Unauthenticated):
        resolve_principal_from_request(request, service)


def test_cookie_malformato_da_errore_tipizzato() -> None:
    service, _ = _service()
    request = _request("non-un-uuid", [])
    with pytest.raises(Unauthenticated):
        resolve_principal_from_request(request, service)
