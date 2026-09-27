"""DTO pubblici dei run durevoli (P-05).

Lo snapshot completo non è esposto: contiene contesto e istruzioni; la
lettura pubblica riporta solo campi utili al client. Le evenienze
(replay, streaming) restano a P-06.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from newray.modules.conversations import MAX_MESSAGE_LENGTH
from newray.modules.runs import RunState


class CreateRunRequest(BaseModel):
    """Creazione di un run durevole: il worker persiste prompt e risposta
    solo a fine terminale. ``idempotency_key`` è obbligatoria: il retry
    della stessa chiave restituisce lo stesso run senza duplicare code.
    """

    content: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    profile_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=96)


class RunSnapshotDTO(BaseModel):
    """Vista sintetica di un run durevole (senza contesto interno)."""

    id: uuid.UUID
    conversation_id: uuid.UUID
    state: RunState
    idempotency_key: str
    partial_text: str
    finish_reason: str | None
    error_code: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    eval_duration_ns: int | None
    model_name: str
    digest: str | None
    profile_id: uuid.UUID
    profile_version_id: uuid.UUID
    binding_id: uuid.UUID
    context_message_count: int
    context_character_count: int
    context_truncated: bool
    max_output_tokens: int | None
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    heartbeat_at: datetime | None
    fence: int
