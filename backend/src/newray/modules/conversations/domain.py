"""Entità di conversazioni e messaggi (NewRay.md §7.2).

Invarianti: proprietà esplicita (organizzazione, utente proprietario),
messaggi con sequenza strettamente crescente per conversazione. La
sequenza la assegna la persistenza alla scrittura
(``MessageStore.append``), non l'applicazione: il dominio la valida.

In B-01 i messaggi sono dell'utente o dell'assistente: i passi interni
di un run sono eventi ``run_event`` (B-04/B-06), non messaggi della
conversazione.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

#: Limiti di titolo e messaggio: fonte unica per dominio e DTO
#: (NewRay.md §19.2). Il limite dei messaggi si legherà al budget di
#: contesto in B-04.
MAX_TITLE_LENGTH = 200
MAX_MESSAGE_LENGTH = 50_000


class MessageRole(StrEnum):
    """Autore del messaggio, noto al server: mai derivato dal client (§7.1)."""

    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class Conversation:
    """Conversazione di lavoro: un solo proprietario utente (§7.2)."""

    id: uuid.UUID
    organization_id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not 1 <= len(self.title) <= MAX_TITLE_LENGTH:
            raise ValueError(f"Conversazione: titolo fuori dai limiti (1-{MAX_TITLE_LENGTH})")


@dataclass(frozen=True, slots=True)
class Message:
    """Messaggio in conversazione: sequenza strettamente crescente."""

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: MessageRole
    content: str
    sequence: int
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("Message: contenuto vuoto")
        if len(self.content) > MAX_MESSAGE_LENGTH:
            raise ValueError("Message: contenuto oltre il limite")
        if self.sequence < 1:
            raise ValueError("Message: la sequenza deve essere >= 1")
