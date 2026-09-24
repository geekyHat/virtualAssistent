"""DTO pubblici di conversazioni e messaggi (NewRay.md §19.2; B-01).

I limiti (titolo, contenuto) provengono dal dominio come fonte unica:
il DTO riflette i vincoli, il dominio li riafferma ai confini interni.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from newray.modules.conversations import (
    MAX_MESSAGE_LENGTH,
    MAX_TITLE_LENGTH,
    MessageRole,
)


def _reject_whitespace_only(v: str) -> str:
    if not v.strip():
        raise ValueError("il valore non può contenere solo spazi")
    return v


class CreateConversationRequest(BaseModel):
    """Creazione di una conversazione: solo il titolo, il resto è del server."""

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)

    @field_validator("title")
    @classmethod
    def title_not_whitespace(cls, v: str) -> str:
        return _reject_whitespace_only(v)


class RenameConversationRequest(BaseModel):
    """Rinomina di una conversazione.

    ``expected_updated_at`` è la versione attesa (il campo ``updated_at``
    dell'ultima lettura del client): se la conversazione è cambiata nel
    frattempo la mutazione risponde 409, mai una sovrascrittura silenziosa.
    """

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    expected_updated_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def title_not_whitespace(cls, v: str) -> str:
        return _reject_whitespace_only(v)


class ConversationDTO(BaseModel):
    """Conversazione nel profilo pubblico: nessun campo oltre il necessario."""

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationListDTO(BaseModel):
    """Pagina di conversazioni con cursore per la pagina successiva (§19.2)."""

    items: list[ConversationDTO]
    next_cursor: str | None


class CreateMessageRequest(BaseModel):
    """Messaggio dell'utente: il ruolo è deciso dal server, mai dal payload.

    ``idempotency_key`` opzionale: il retry della stessa richiesta
    restituisce lo stesso messaggio senza duplicarlo; stessa chiave con
    contenuto diverso risponde 409.
    """

    content: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("content")
    @classmethod
    def content_not_whitespace(cls, v: str) -> str:
        return _reject_whitespace_only(v)


class MessageDTO(BaseModel):
    """Messaggio della conversazione, con sequenza e ruolo noti al server."""

    id: uuid.UUID
    role: MessageRole
    content: str
    sequence: int
    created_at: datetime


class MessageListDTO(BaseModel):
    """Pagina di messaggi, in ordine di sequenza crescente.

    ``next_sequence`` è l'ultimo valore ricevuto: se presente va passato
    invariato come ``after_sequence`` per ottenere la pagina successiva.
    """

    items: list[MessageDTO]
    next_sequence: int | None
