"""Caso d'uso P-05: creazione idempotente, snapshot e cancel dei run durevoli.

Il worker (``modules/runs/worker.py``) consuma la coda, questo servizio è la
sola porta di ingresso dei run per l'HTTP.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from newray.kernel.clock import Clock, SystemClock
from newray.kernel.errors import Conflict, NotFound
from newray.kernel.identity import Principal, new_id

from .application import InlineRunService
from .durable import DurableRun, RunState, RunStore


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    return value


def _payload_hash(profile_id: uuid.UUID, content: str) -> str:
    payload = json.dumps(
        {"profile_id": str(profile_id), "content": content},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_WALL_DEADLINE = timedelta(minutes=10)


class DurableRunService:
    """Run accodati; il worker in-process/side-car ne esegue la generazione."""

    def __init__(
        self,
        store: RunStore,
        inline: InlineRunService,
        clock: Clock | None = None,
        wall_deadline: timedelta = DEFAULT_WALL_DEADLINE,
        queue_cap: int | None = None,
    ) -> None:
        self._store = store
        self._inline = inline
        self._clock = clock or SystemClock()
        self._wall_deadline = wall_deadline
        self._queue_cap = queue_cap

    async def create(
        self,
        principal: Principal,
        conversation_id: uuid.UUID,
        profile_id: uuid.UUID,
        content: str,
        idempotency_key: str,
    ) -> DurableRun:
        if not 1 <= len(idempotency_key) <= 96:
            raise ValueError("chiave di idempotenza fuori dai limiti")
        payload_hash = _payload_hash(profile_id, content)
        existing = await asyncio.to_thread(
            self._store.find_by_key, principal.scope, conversation_id, idempotency_key
        )
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise Conflict("chiave di idempotenza riutilizzata con richiesta diversa")
            return existing

        prepared = await self._inline.prepare(principal, conversation_id, profile_id, content)
        binding = prepared.binding
        snapshot: dict[str, object] = {
            "profile_id": str(binding.profile_id),
            "profile_version_id": str(binding.profile_version_id),
            "binding_id": str(binding.binding_id),
            "model_name": binding.model_name,
            "runtime": binding.runtime,
            "digest": binding.digest,
            "parameters": _json_value(binding.parameters),
            "instructions": binding.instructions,
            "messages": [
                {"role": str(message.role), "content": message.content}
                for message in prepared.chat_request.messages
            ],
            "prompt": content,
            "max_output_tokens": prepared.chat_request.max_tokens,
            "context_truncated": prepared.context_truncated,
            "context_message_count": prepared.context_message_count,
            "context_character_count": prepared.context_character_count,
            "request_hash": prepared.request_hash,
        }
        now = self._clock.now()
        deadline_at = now + self._wall_deadline
        run = DurableRun(
            id=new_id(),
            conversation_id=conversation_id,
            organization_id=principal.organization_id,
            owner_id=principal.user_id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            state=RunState.QUEUED,
            snapshot=snapshot,
            partial_text="",
            finish_reason=None,
            prompt_tokens=None,
            completion_tokens=None,
            eval_duration_ns=None,
            lease_owner=None,
            lease_until=None,
            fence=0,
            created_at=now,
            updated_at=now,
            deadline_at=deadline_at,
        )
        return await asyncio.to_thread(self._store.enqueue, run, queue_cap=self._queue_cap)

    async def get(self, principal: Principal, run_id: uuid.UUID) -> DurableRun:
        run = await asyncio.to_thread(self._store.get, principal.scope, run_id)
        if run is None:
            raise NotFound("run non trovato")
        return run

    async def request_cancel(self, principal: Principal, run_id: uuid.UUID) -> DurableRun:
        now = self._clock.now()
        return await asyncio.to_thread(
            self._store.mark_cancel_requested, principal.scope, run_id, now
        )


def utc_now() -> datetime:
    """Alias esplicito UTC per i test fake."""
    return datetime.now(UTC)
