"""Modulo identity: utenti, sessioni e membership (NewRay.md §6).

Non sceglie il profilo AI: l'asse AI appartiene al modulo ``profiles``.
L'API pubblica è ``public.py``; qui si riesportano solo i nomi pubblici,
così ``from newray.modules.identity import ...`` resta l'unico punto di
import per i consumatori (mai ``adapters`` né tabelle private altrui).
"""

from newray.modules.identity.public import (
    DEFAULT_SESSION_TTL,
    PERSONAL_ORG_NAME,
    IdentityService,
    Organization,
    OwnerBootstrap,
    Session,
    SessionStore,
    User,
    UserRepository,
    normalize_display_name,
)

__all__ = [
    "DEFAULT_SESSION_TTL",
    "PERSONAL_ORG_NAME",
    "IdentityService",
    "Organization",
    "OwnerBootstrap",
    "Session",
    "SessionStore",
    "User",
    "UserRepository",
    "normalize_display_name",
]
