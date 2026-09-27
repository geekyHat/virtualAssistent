"""Contratto durevole del run testuale; nessuna dipendenza da HTTP o SQL."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from newray.kernel.errors import DomainError
from newray.kernel.identity import Scope


class LeaseLost(DomainError):
    """Il worker non detiene più il fence: un altro worker ha preso il run."""

    code = "LEASE_LOST"


class RunAlreadyTerminal(DomainError):
    """Il run è già in stato terminale; nessuna nuova scrittura ammessa."""

    code = "RUN_ALREADY_TERMINAL"


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_STATES = frozenset(
    {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED, RunState.INTERRUPTED}
)


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
    deadline_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    heartbeat_at: datetime | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot", freeze_json(self.snapshot))

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


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
    """Ricevuta di claim: worker vincolato a run e fence.

    Il fence emesso qui è l'unico valido per checkpoint/finalize/heartbeat
    del worker: un vecchio worker con fence obsoleto non può più scrivere.
    """

    run: DurableRun
    fence: int


class ComputeLease(Protocol):
    """Serializza l'accesso alla risorsa di inference (GPU) fra processi
    sullo stesso host: solo il detentore corrente può aprire uno stream.

    Il default di produzione è un lock avvisorio a livello file system
    (rilasciato automaticamente dal kernel se il processo muore, quindi il
    recupero è naturale). I test usano fake in memoria; un no-op esiste per
    i test unit che non simulano la contesa.
    """

    def try_acquire(self) -> bool:
        """Prova a prendere il lease; ``False`` se un altro lo detiene."""
        ...

    def release(self) -> None:
        """Rilascia il lease se detenuto (idempotente)."""
        ...


class NoopComputeLease:
    """Compute lease che concede sempre: solo per i test unit che
    non simulano contesa e per contesti single-process controllati."""

    def try_acquire(self) -> bool:
        return True

    def release(self) -> None:
        return None


class RunStore(Protocol):
    def enqueue(self, run: DurableRun) -> DurableRun:
        """Atomicità della ricevuta scoped; payload diverso = Conflict."""
        ...

    def get(self, scope: Scope, run_id: uuid.UUID) -> DurableRun | None: ...

    def find_by_key(
        self, scope: Scope, conversation_id: uuid.UUID, idempotency_key: str
    ) -> DurableRun | None: ...

    def claim_next(
        self,
        *,
        worker_id: uuid.UUID,
        lease_until: datetime,
        now: datetime,
    ) -> ClaimedRun | None:
        """Prende in carico atomicamente un run `queued` o un `running` con
        lease scaduto; incrementa il fence per invalidare il vecchio worker.
        """
        ...

    def heartbeat(
        self,
        *,
        scope: Scope,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        lease_until: datetime,
        now: datetime,
    ) -> DurableRun: ...

    def checkpoint(
        self,
        *,
        scope: Scope,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        partial_text: str,
        lease_until: datetime,
        now: datetime,
    ) -> DurableRun: ...

    def complete(
        self,
        *,
        scope: Scope,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        partial_text: str,
        finish_reason: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        eval_duration_ns: int | None,
        now: datetime,
    ) -> DurableRun: ...

    def fail(
        self,
        *,
        scope: Scope,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        error_code: str,
        finish_reason: str,
        partial_text: str,
        now: datetime,
    ) -> DurableRun: ...

    def mark_cancel_requested(
        self, scope: Scope, run_id: uuid.UUID, now: datetime
    ) -> DurableRun: ...

    def finalize_cancelled(
        self,
        *,
        scope: Scope,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        partial_text: str,
        now: datetime,
    ) -> DurableRun: ...

    def interrupt_stale(
        self,
        *,
        scope: Scope,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        now: datetime,
    ) -> DurableRun: ...
