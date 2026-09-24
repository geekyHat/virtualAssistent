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


#: Ragioni di terminazione esplicite (``finish_reason``). Il valore
#: ``WORKER_LOST`` è scritto anche dalla funzione SQL
#: ``newray_reclaim_stale_runs`` (migrazione 0010): deve restare identico.
FINISH_REASON_EMPTY_OUTPUT = "empty_output"
FINISH_REASON_DEADLINE_EXCEEDED = "deadline_exceeded"
FINISH_REASON_MODEL_ERROR = "model_error"
FINISH_REASON_WORKER_LOST = "worker_lost"


class RunStore(Protocol):
    def enqueue(self, run: DurableRun) -> DurableRun:
        """Atomicità della ricevuta scoped; payload diverso = Conflict."""
        ...

    def get(self, scope: Scope, run_id: uuid.UUID) -> DurableRun | None: ...

    def find_by_key(
        self, scope: Scope, conversation_id: uuid.UUID, idempotency_key: str
    ) -> DurableRun | None: ...

    def count_active(self, scope: Scope) -> int:
        """Run ``queued``/``running`` nello scope: base della coda limitata.

        Limite per scope (organizzazione/proprietario), non globale: il
        pilot è a singolo proprietario per installazione (ADR 0002); una
        coda di sistema condivisa fra più organizzazioni resta F-09.
        """
        ...

    def claim(
        self, worker_id: uuid.UUID, lease_seconds: int, resource_id: str
    ) -> DurableRun | None:
        """Reclama al più un run ``queued`` di qualunque scope (SKIP LOCKED).

        Attraversa lo scope tramite la funzione SQL SECURITY DEFINER
        ``newray_claim_run`` (ADR 0007): nessun ruolo applicativo ottiene
        BYPASSRLS. ``None`` se non c'è lavoro o la risorsa è occupata.
        """
        ...

    def heartbeat(
        self,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        lease_seconds: int,
        partial_text: str,
        resource_id: str,
    ) -> DurableRun | None:
        """Rinnova lease e persiste il checkpoint; ``None`` se il fencing
        è fallito (worker scaduto/sostituito): il chiamante deve interrompere
        lo stream e non scrivere altro come vincitore."""
        ...

    def finalize(
        self,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        state: RunState,
        finish_reason: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        eval_duration_ns: int | None,
        partial_text: str,
        resource_id: str,
    ) -> DurableRun | None:
        """Transizione terminale fencing-checked; ``None`` se un worker più
        recente ha già reclamato il run (vecchio worker non può finalizzare)."""
        ...

    def reclaim_stale(self, grace_seconds: int) -> tuple[DurableRun, ...]:
        """Run ``running`` con lease scaduto e worker verificato morto
        (``run_workers`` senza heartbeat recente) → ``interrupted``."""
        ...

    def register_worker(self, worker_id: uuid.UUID, pid: int, hostname: str) -> None:
        """Upsert della liveness del worker (nessun dato di tenant)."""
        ...

    def touch_worker(self, worker_id: uuid.UUID) -> None:
        """Rinnova la liveness del worker, anche se inattivo fra un claim
        e l'altro: distingue un worker idle da uno morto per il reclaim."""
        ...
