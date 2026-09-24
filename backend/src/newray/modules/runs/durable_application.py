"""P-05: creazione idempotente e lettura di snapshot dei run durevoli.

Il consumo della coda (claim/heartbeat/fencing/finalize) è in ``worker.py``;
questo servizio resta la sola porta di scrittura per la creazione e la
sola porta di lettura scoped per lo snapshot, usate dalla route HTTP.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

from newray.kernel.errors import Conflict, NotFound, QueueFull
from newray.kernel.identity import Principal, new_id

from .application import InlineRunService
from .durable import DurableRun, EventPage, RunState, RunStore

#: Limite di coda per scope (organizzazione/proprietario), non globale: il
#: pilot è a singolo proprietario per installazione (ADR 0002). Valore
#: iniziale dichiarato (NewRay.md §8.4 non fissa numeri), da tarare in
#: P-19/P-20. La risoluzione da ``NEWRAY_RUNS_MAX_QUEUE_DEPTH`` è nel
#: bootstrap (``Settings``), mai letta qui: l'applicazione resta senza I/O
#: d'ambiente diretto.
DEFAULT_MAX_QUEUE_DEPTH = 50


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


class DurableRunService:
    """Run accodati; nessun avvio automatico fino al worker P-05."""

    def __init__(
        self,
        store: RunStore,
        inline: InlineRunService,
        *,
        max_queue_depth: int = DEFAULT_MAX_QUEUE_DEPTH,
    ) -> None:
        self._store = store
        self._inline = inline
        self._max_queue_depth = max_queue_depth

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

        active = await asyncio.to_thread(self._store.count_active, principal.scope)
        if active >= self._max_queue_depth:
            raise QueueFull("coda dei run al limite: riprovare più tardi")

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
        }
        now = datetime.now(UTC)
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
            cancel_requested_at=None,
            created_at=now,
            updated_at=now,
        )
        return await asyncio.to_thread(self._store.enqueue, run)

    async def get(self, principal: Principal, run_id: uuid.UUID) -> DurableRun:
        run = await asyncio.to_thread(self._store.get, principal.scope, run_id)
        if run is None:
            raise NotFound("run non trovato")
        return run

    async def active_for_conversation(
        self, principal: Principal, conversation_id: uuid.UUID
    ) -> DurableRun | None:
        """Run non terminale più recente della conversazione, o ``None``
        (P-06: resume — una scheda nuova o un refresh ritrovano il run in
        corso senza già conoscerne l'id, senza inventare un esito)."""
        return await asyncio.to_thread(
            self._store.find_active_by_conversation, principal.scope, conversation_id
        )

    async def cancel(self, principal: Principal, run_id: uuid.UUID) -> DurableRun:
        """Richiesta di cancellazione persistita e idempotente (P-06)."""
        run = await asyncio.to_thread(self._store.request_cancel, principal.scope, run_id)
        if run is None:
            raise NotFound("run non trovato")
        return run

    async def events(
        self, principal: Principal, run_id: uuid.UUID, after_sequence: int
    ) -> EventPage:
        """Eventi oltre il cursore, scoped (P-06); ``gap=True`` → resync."""
        return await asyncio.to_thread(
            self._store.list_events, principal.scope, run_id, after_sequence
        )
