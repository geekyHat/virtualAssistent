"""DTO dei run durevoli (P-05).

Il payload è simmetrico con la route inline (``chat.py``): stesso
``profile_id`` + ``content``, ma la ricevuta è persistita e la generazione
avviene fuori dal ciclo richiesta HTTP. Il client legge lo stato via
``GET /runs/{id}`` (snapshot); gli eventi in streaming appartengono a P-06.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from newray.modules.conversations import MAX_MESSAGE_LENGTH


class CreateRunRequest(BaseModel):
    """Richiesta di creazione run: contenuto utente e profilo scelto."""

    content: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    profile_id: uuid.UUID


class RunSnapshotDTO(BaseModel):
    """Snapshot pubblico di un run durevole.

    Espone gli stati definiti in §8 e la contabilizzazione dell'esito.
    Il campo ``snapshot`` interno (input/binding congelati) resta privato
    del backend; qui riportiamo solo model_name e digest utili al client.
    """

    id: uuid.UUID
    conversation_id: uuid.UUID
    state: str
    finish_reason: str | None
    partial_text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    eval_duration_ns: int | None
    model_name: str | None
    digest: str | None
    context_truncated: bool | None
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
