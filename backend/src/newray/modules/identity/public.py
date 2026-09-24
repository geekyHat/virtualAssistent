"""API pubblica del modulo identity (NewRay.md §5.1).

Questo è l'unico punto di import per gli altri moduli e per il bootstrap:
mai importare ``adapters`` o tabelle private da qui.
"""

from newray.modules.identity.application import (
    DEFAULT_SESSION_TTL,
    PERSONAL_ORG_NAME,
    IdentityService,
    normalize_display_name,
)
from newray.modules.identity.domain import Organization, Session, User
from newray.modules.identity.ports import OwnerBootstrap, SessionStore, UserRepository

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
