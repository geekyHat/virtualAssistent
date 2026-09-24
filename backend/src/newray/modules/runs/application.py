"""Orchestrazione della preview inline, confinata fino a B-04/B-05.

Owner: ``runs``. Il servizio usa soltanto le API pubbliche di conversations,
profiles e models. Le operazioni sincrone di persistenza sono interamente
offloadate; nessuna transazione resta aperta durante inference.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from newray.kernel.errors import InferenceFailed
from newray.kernel.identity import Principal
from newray.modules.conversations import ConversationService, Message, MessageRole
from newray.modules.models import (
    ChatMessage,
    ChatModel,
    ChatRequest,
    ChatRole,
    Completion,
    ContentDelta,
)
from newray.modules.profiles import ProfileService, ResolvedBinding

from .domain import InlineCompleted, InlineDelta, InlineRunEvent, PreparedInlineRun

# Budget transitorio e dichiarato. B-04 lo porterà nella configurazione
# identificata dello snapshot; qui elimina il precedente ``limit=200`` opaco.
INLINE_CONTEXT_MAX_MESSAGES = 50
INLINE_CONTEXT_MAX_CHARACTERS = 100_000
INLINE_MAX_OUTPUT_TOKENS = 2_048


class InlineRunService:
    """Prepara, genera e persiste atomicamente la preview transitoria."""

    def __init__(
        self,
        conversations: ConversationService,
        profiles: ProfileService,
        chat_model: ChatModel,
    ) -> None:
        self._conversations = conversations
        self._profiles = profiles
        self._chat_model = chat_model

    async def prepare(
        self,
        principal: Principal,
        conversation_id: uuid.UUID,
        profile_id: uuid.UUID,
        content: str,
    ) -> PreparedInlineRun:
        """Valida tutto prima di scrivere il prompt.

        La conversazione e il profilo sono risolti dal principal server-side;
        un modello assente fallisce qui, prima che esista qualunque messaggio.
        """
        await asyncio.to_thread(self._conversations.get_conversation, principal, conversation_id)
        binding = await self._profiles.resolve_binding(principal, profile_id, assistant_only=True)
        history = await asyncio.to_thread(
            self._conversations.list_recent_messages,
            principal,
            conversation_id,
            limit=INLINE_CONTEXT_MAX_MESSAGES + 1,
        )
        messages, truncated, characters = self._compose_context(binding, history, content)
        request_hash = _request_hash(conversation_id, binding, content)
        return PreparedInlineRun(
            conversation_id=conversation_id,
            content=content,
            binding=binding,
            chat_request=ChatRequest(
                model=binding.model_name,
                runtime=binding.runtime,
                messages=messages,
                parameters=dict(binding.parameters),
                max_tokens=INLINE_MAX_OUTPUT_TOKENS,
            ),
            request_hash=request_hash,
            context_message_count=len(messages),
            context_character_count=characters,
            context_truncated=truncated,
        )

    async def execute(
        self,
        principal: Principal,
        prepared: PreparedInlineRun,
        *,
        idempotency_key: str | None,
    ) -> AsyncIterator[InlineRunEvent]:
        """Genera sul binding preparato e salva solo una coppia completa.

        Con ricevuta esistente non richiama il modello. Errori, EOF e cancel
        non lasciano un prompt orfano. ``length`` è un terminale onesto e
        persiste il testo parziale prodotto dal runtime.
        """
        if idempotency_key is not None:
            existing = await asyncio.to_thread(
                self._conversations.find_exchange,
                principal,
                prepared.conversation_id,
                idempotency_key,
                prepared.request_hash,
            )
            if existing is not None:
                user_message, assistant_message = existing
                yield InlineDelta(assistant_message.content)
                yield InlineCompleted(
                    user_message=user_message,
                    assistant_message=assistant_message,
                    completion=None,
                    replayed=True,
                )
                return

        accumulated = ""
        completion: Completion | None = None
        stream = self._chat_model.stream(prepared.chat_request)
        try:
            async for event in stream:
                if isinstance(event, ContentDelta):
                    if completion is not None:
                        raise InferenceFailed("contenuto ricevuto dopo il terminale del modello")
                    accumulated += event.text
                    yield InlineDelta(event.text)
                elif isinstance(event, Completion):
                    if completion is not None:
                        raise InferenceFailed("terminale duplicato dal modello")
                    completion = event
        finally:
            close: Any = getattr(stream, "aclose", None)
            if close is not None:
                await close()

        if completion is None:
            raise InferenceFailed("stream del modello terminato senza completamento")
        if not accumulated:
            raise InferenceFailed("il modello non ha prodotto contenuto visibile")

        user_message, assistant_message = await asyncio.to_thread(
            self._conversations.add_exchange,
            principal,
            prepared.conversation_id,
            prepared.content,
            accumulated,
            idempotency_key=idempotency_key,
            request_hash=prepared.request_hash if idempotency_key is not None else None,
        )
        yield InlineCompleted(
            user_message=user_message,
            assistant_message=assistant_message,
            completion=completion,
            replayed=False,
        )

    @staticmethod
    def _compose_context(
        binding: ResolvedBinding,
        history: list[Message],
        content: str,
    ) -> tuple[tuple[ChatMessage, ...], bool, int]:
        fixed: list[ChatMessage] = []
        characters = len(content)
        if binding.instructions:
            fixed.append(ChatMessage(ChatRole.SYSTEM, binding.instructions))
            characters += len(binding.instructions)
        # Il prompt corrente non viene ancora persistito, ma è sempre l'ultimo
        # dato user del contesto.
        budget = max(0, INLINE_CONTEXT_MAX_CHARACTERS - characters)
        selected: list[Message] = []
        used = 0
        for message in reversed(history[-INLINE_CONTEXT_MAX_MESSAGES:]):
            if used + len(message.content) > budget:
                break
            selected.append(message)
            used += len(message.content)
        selected.reverse()
        truncated = len(selected) < len(history)
        for message in selected:
            role = ChatRole.USER if message.role is MessageRole.USER else ChatRole.ASSISTANT
            fixed.append(ChatMessage(role, message.content))
        fixed.append(ChatMessage(ChatRole.USER, content))
        return tuple(fixed), truncated, characters + used


def _request_hash(
    conversation_id: uuid.UUID,
    binding: ResolvedBinding,
    content: str,
) -> str:
    normalized = json.dumps(
        {
            "conversation_id": str(conversation_id),
            "profile_id": str(binding.profile_id),
            "profile_version_id": str(binding.profile_version_id),
            "binding_id": str(binding.binding_id),
            "model": binding.model_name,
            "digest": binding.digest,
            "parameters": dict(binding.parameters),
            "content": content,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
