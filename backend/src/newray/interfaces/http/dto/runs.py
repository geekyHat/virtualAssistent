"""DTO pubblici dei run durevoli (P-05, NewRay.md §19.3).

Contratto minimale e stabile: solo lo stato osservabile del run, mai lo
snapshot interno (binding/digest/parametri) né segreti. Eventi/stream e
cancellazione restano P-06.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from newray.modules.conversations import MAX_MESSAGE_LENGTH
from newray.modules.runs import RunState


def _reject_whitespace_only(v: str) -> str:
    if not v.strip():
        raise ValueError("il valore non può contenere solo spazi")
    return v


class CreateRunRequest(BaseModel):
    """Creazione di un run durevole: profilo, messaggio e idempotenza.

    ``idempotency_key`` è obbligatoria: il retry della stessa richiesta
    restituisce lo stesso run senza rieseguirlo; stessa chiave con
    contenuto/profilo diverso risponde 409.
    """

    profile_id: uuid.UUID
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    idempotency_key: str = Field(min_length=1, max_length=96)

    @field_validator("content")
    @classmethod
    def content_not_whitespace(cls, v: str) -> str:
        return _reject_whitespace_only(v)


class RunDTO(BaseModel):
    """Snapshot pubblico del run: stato e progresso, non il binding interno."""

    id: uuid.UUID
    conversation_id: uuid.UUID
    state: RunState
    partial_text: str
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    eval_duration_ns: int | None
    created_at: datetime
    updated_at: datetime


class RunToolInvocationDTO(BaseModel):
    """Ricevuta pubblica di una invocazione tool (P-07, NewRay.md §8.3).

    Espone l'esito osservabile: stato, risultato ed eventuale codice di
    errore. ``result`` è il contenuto restituito dal tool (già dato, non
    istruzione); il payload interno del gateway non compare qui.
    """

    id: uuid.UUID
    call_id: str
    tool_name: str
    state: str
    result: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class RunToolInvocationsDTO(BaseModel):
    """Elenco delle ricevute tool di un run, in ordine di creazione."""

    items: list[RunToolInvocationDTO]
