"""Valori del run inline transitorio (NewRay.md §§8.1–8.3; B-03.2-36).

Questi tipi rendono esplicito il confine oggi disponibile senza fingere che
la preview HTTP sia già il run durevole B-04–B-07. Non contengono framework,
provider o persistenza.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from newray.modules.conversations import Message
from newray.modules.models import ChatRequest, Completion
from newray.modules.profiles import ResolvedBinding


@dataclass(frozen=True, slots=True)
class PreparedInlineRun:
    """Richiesta validata, con binding e contesto già risolti."""

    conversation_id: uuid.UUID
    content: str
    binding: ResolvedBinding
    chat_request: ChatRequest
    request_hash: str
    context_message_count: int
    context_character_count: int
    context_truncated: bool


@dataclass(frozen=True, slots=True)
class InlineDelta:
    """Frammento visibile prodotto dallo stesso modello del binding."""

    text: str


@dataclass(frozen=True, slots=True)
class InlineCompleted:
    """Esito terminale persistito oppure replay di una ricevuta esistente."""

    user_message: Message
    assistant_message: Message
    completion: Completion | None
    replayed: bool


InlineRunEvent = InlineDelta | InlineCompleted
