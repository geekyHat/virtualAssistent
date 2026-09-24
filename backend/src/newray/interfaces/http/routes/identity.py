"""Route identità: bootstrap monouso, login, utente corrente, revoca.

Superficie ``/session`` + ``/session/login`` + ``/session/status`` +
``/me`` (NewRay.md §19.2, §20.3; B-03.2-14). Il principal viene sempre
risolto dal server a partire dal cookie opaco; gli identificativi del
client non sono mai letti.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from newray.interfaces.http.dto.identity import (
    BootstrapRequest,
    IdentityDTO,
    LoginRequest,
    SessionStatusDTO,
)
from newray.interfaces.http.middleware.identity import SESSION_COOKIE, require_principal
from newray.interfaces.http.routes import API_PREFIX
from newray.kernel.identity import Principal
from newray.modules.identity import DEFAULT_SESSION_TTL, IdentityService


def _identity(principal: Principal, display_name: str) -> IdentityDTO:
    return IdentityDTO(
        user_id=principal.user_id,
        display_name=display_name,
        role=principal.role,
    )


def _set_session_cookie(response: Response, session_id: str, cookie_secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=int(DEFAULT_SESSION_TTL.total_seconds()),
        path="/",
        httponly=True,
        samesite="lax",
        secure=cookie_secure,
    )


def build_router(cookie_secure: bool) -> APIRouter:
    """Costruisce le route identità con l'attributo cookie di ambiente.

    ``cookie_secure`` deriva dalla configurazione risolta (True quando
    l'origine è servita in HTTPS, §20.3); in sviluppo loopback HTTP è
    False, in ufficio/produzione deve essere True.
    """
    router = APIRouter(prefix=API_PREFIX)

    @router.get("/session/status", response_model=SessionStatusDTO)
    def status(request: Request) -> SessionStatusDTO:
        """Stato pubblico dell'installazione (B-03.2-14).

        Nessuna informazione derivata dal cookie o dall'owner reale:
        solo la presenza di un proprietario. Serve alla WebUI per
        decidere fra schermata bootstrap e schermata login senza
        tentare bootstrap e leggere il ``CONFLICT`` come segnale.
        """
        service: IdentityService = request.app.state.identity_service
        return SessionStatusDTO(bootstrapped=service.is_bootstrapped())

    @router.post("/session", status_code=201, response_model=IdentityDTO)
    def bootstrap(body: BootstrapRequest, request: Request, response: Response) -> IdentityDTO:
        """Bootstrap locale monouso del proprietario (NewRay.md §20.3).

        Crea organizzazione, proprietario e sessione, ed emette il cookie
        opaco con HttpOnly/SameSite. Se il proprietario esiste già,
        ``CONFLICT`` (409): il bootstrap non può essere ripetuto e non
        accetta il solo nome come credenziale (B-03.2-14).
        """
        service: IdentityService = request.app.state.identity_service
        principal = service.bootstrap_owner(body.display_name, body.credential)
        user = service.current_user(principal)
        _set_session_cookie(response, str(principal.session_id), cookie_secure)
        return _identity(principal, user.display_name)

    @router.post("/session/login", status_code=200, response_model=IdentityDTO)
    def login(body: LoginRequest, request: Request, response: Response) -> IdentityDTO:
        """Rientro autenticato del proprietario esistente (B-03.2-14).

        Ogni chiamata riuscita apre una **nuova** sessione: le
        precedenti restano gestibili con la revoca; nessun riuso del
        cookie in circolo. Errore uniforme ``INVALID_CREDENTIALS`` per
        owner assente o credenziale sbagliata; ``CREDENTIAL_NOT_SET`` è
        il segnale al percorso di recupero locale, non un login riuscito.
        """
        service: IdentityService = request.app.state.identity_service
        principal = service.login(body.display_name, body.credential)
        user = service.current_user(principal)
        _set_session_cookie(response, str(principal.session_id), cookie_secure)
        return _identity(principal, user.display_name)

    @router.get("/me", response_model=IdentityDTO)
    def me(request: Request, principal: Principal = Depends(require_principal)) -> IdentityDTO:
        """Utente corrente: identità risolta dal server, mai dal client."""
        service: IdentityService = request.app.state.identity_service
        user = service.current_user(principal)
        return _identity(principal, user.display_name)

    @router.post("/session/revoke", status_code=204)
    def revoke(
        request: Request, response: Response, principal: Principal = Depends(require_principal)
    ) -> None:
        """Revoca immediata e idempotente della sessione in corso (§22.2 #4)."""
        service: IdentityService = request.app.state.identity_service
        service.revoke_session(principal)
        response.delete_cookie(
            SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=cookie_secure
        )

    return router
