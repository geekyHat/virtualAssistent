"""Contratto durevole del run testuale; nessuna dipendenza da HTTP o SQL."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from newray.kernel.identity import Scope


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class DurableRun:
    id: uuid.UUID
    conversation_id: uuid.UUID
    organization_id: uuid.UUID
    owner_id: uuid.UUID
    idempotency_key: str
    payload_hash: str
    state: RunState
    snapshot: Mapping[str, object]
    partial_text: str
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    eval_duration_ns: int | None
    lease_owner: uuid.UUID | None
    lease_until: datetime | None
    fence: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot", freeze_json(self.snapshot))


def freeze_json(value: object) -> object:
    """Copia profonda senza alias mutabili dello snapshot del run."""
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("snapshot con chiavi non stringa")
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, tuple | list):
        return tuple(freeze_json(item) for item in value)
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise ValueError("snapshot non JSON-serializzabile")


def thaw_json(value: object) -> object:
    """Restituisce soli dict/list/primitivi per l'encoder JSON dell'adapter."""
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    """Run acquisito da un worker: identifica lease e fence attivi.

    ``fence`` è il valore osservato dopo l'incremento del claim; il worker
    deve rifiutare ogni scrittura successiva se il valore in DB è diverso
    (lease scaduto e riassegnato).
    """

    run: DurableRun
    worker_id: uuid.UUID
    lease_until: datetime
    fence: int


class RunStore(Protocol):
    def enqueue(self, run: DurableRun) -> DurableRun:
        """Atomicità della ricevuta scoped; payload diverso = Conflict."""
        ...

    def get(self, scope: Scope, run_id: uuid.UUID) -> DurableRun | None: ...

    def find_by_key(
        self, scope: Scope, conversation_id: uuid.UUID, idempotency_key: str
    ) -> DurableRun | None: ...

    def claim(
        self,
        worker_id: uuid.UUID,
        lease_duration_seconds: int,
        now: datetime,
    ) -> ClaimedRun | None:
        """Acquisisce atomicamente un run pronto (queued o lease scaduto).

        Bumpa ``fence`` e imposta lease/lease_until per identificare la
        generazione corrente. Nessuna altra transazione può finalizzare un
        run con fence obsoleto.
        """
        ...

    def renew_lease(
        self,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        lease_until: datetime,
        now: datetime,
    ) -> bool:
        """Prolunga il lease se il worker detiene ancora la generazione.

        Ritorna ``False`` se un altro claim ha bumpato ``fence``: il worker
        deve interrompere l'esecuzione e non finalizzare.
        """
        ...

    def save_partial(
        self,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        partial_text: str,
        now: datetime,
    ) -> bool:
        """Checkpoint incrementale del testo parziale (solo lease vivo)."""
        ...

    def finalize(
        self,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        state: RunState,
        finish_reason: str | None,
        partial_text: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        eval_duration_ns: int | None,
        now: datetime,
    ) -> bool:
        """Chiude il run in stato terminale se il lease è ancora valido.

        Ritorna ``False`` su fence obsoleto o lease già liberato; il worker
        non deve trattare quel caso come successo (§P-05).
        """
        ...
