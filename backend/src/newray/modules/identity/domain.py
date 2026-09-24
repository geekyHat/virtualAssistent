"""Entità dell'identità: organizzazione, utente, sessione.

Invarianti (NewRay.md §7.2): sessione revocabile e a scadenza, nessun ruolo
fornito dal browser. In questa fase (A-02, edizione personale) l'utente
porta direttamente organizzazione e ruolo: la membership separata nasce con
l'edizione ufficio (G), evoluzione dichiarata del contratto.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from newray.kernel.identity import Role


@dataclass(frozen=True, slots=True)
class Organization:
    """Organizzazione: nell'installazione personale è unica (NewRay.md §2.2)."""

    id: uuid.UUID
    display_name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class User:
    """Account locale: identità persistente, mai derivata dal client.

    ``credential_hash`` contiene il record hash della password locale
    (formato canonico di ``newray.kernel.crypto``). È opzionale per un
    unico motivo di compatibilità: gli owner creati prima di B-03.2-14
    non hanno hash. Un login su tale record non è un rifiuto generico
    (``INVALID_CREDENTIALS``) ma un ``CREDENTIAL_NOT_SET`` — recupero
    esplicito, mai ricreazione silenziosa dell'owner.
    """

    id: uuid.UUID
    organization_id: uuid.UUID
    display_name: str
    role: Role
    created_at: datetime
    credential_hash: str | None = None


@dataclass(slots=True)
class Session:
    """Sessione server-side: ID opaco, scadenza e revoca (NewRay.md §7.2, 20.3).

    ``revoked`` è l'unico campo mutabile: la revoca è un fatto di stato,
    non una nuova sessione. La scadenza è valutata con il clock iniettato.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    organization_id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    revoked: bool = field(default=False)

    def is_active(self, now: datetime) -> bool:
        """Attiva se non revocata e non scaduta."""
        return not self.revoked and now < self.expires_at
