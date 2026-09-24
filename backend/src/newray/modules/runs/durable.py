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

from .tool_gateway import ToolInvocationState


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
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot", freeze_json(self.snapshot))


@dataclass(frozen=True, slots=True)
class RunEvent:
    """Voce del log/outbox degli eventi (NewRay.md §19.3, §7.4).

    Appesa dalle stesse transazioni fencing-checked di claim/heartbeat/
    finalize/reclaim (migrazione 0011): "terminale unico" eredita quella
    barriera, non è verificato qui.
    """

    id: uuid.UUID
    run_id: uuid.UUID
    sequence: int
    type: str
    payload: Mapping[str, object]
    occurred_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", freeze_json(self.payload))


@dataclass(frozen=True, slots=True)
class EventPage:
    """Pagina di eventi da un cursore, o segnale di risincronizzazione.

    ``gap=True`` significa che eventi fra il cursore richiesto e il primo
    restituito non esistono più (potati, §19.3 contro buffer illimitati):
    il chiamante deve trattarlo come "cursore scaduto" e proporre uno
    snapshot (``resync``), non fidarsi di ``events`` per riempire il buco.
    """

    events: tuple[RunEvent, ...]
    gap: bool
    #: Sequenza più alta esistente per il run (indipendente da ``events``,
    #: che può essere vuoto): permette al chiamante di calcolare il cursore
    #: dopo un ``resync`` senza una seconda interrogazione.
    latest_sequence: int


#: Tipi di evento minimi emessi in questo pilot (NewRay.md §19.3). I tipi
#: legati a tool/fonti/approvazione (``run.waiting_approval``, ``tool.*``,
#: ``sources.updated``, ``artifact.updated``) non sono emessi: nessun tool
#: prima di P-07, nessuna fonte prima di P-11.
EVENT_RUN_QUEUED = "run.queued"
EVENT_RUN_STARTED = "run.started"
EVENT_MESSAGE_DELTA = "message.delta"
EVENT_RUN_COMPLETED = "run.completed"
EVENT_RUN_FAILED = "run.failed"
EVENT_RUN_CANCELLED = "run.cancelled"
EVENT_RUN_INTERRUPTED = "run.interrupted"
#: Eventi del ciclo tool (P-07, NewRay.md §§8.1, 19.3). Non potati (non
#: sono ``message.delta``). ``run.waiting_approval`` e gli eventi di
#: fonte/artefatto di §19.3 restano non emessi: approval gated e nessuna
#: fonte prima di P-11.
EVENT_TOOL_EXECUTING = "tool.executing"
EVENT_TOOL_SUCCEEDED = "tool.succeeded"
EVENT_TOOL_FAILED = "tool.failed"


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Ricevuta durevole di una invocazione tool (NewRay.md §8.3).

    Idempotente per ``(run_id, call_id)``: una transizione di stato sulla
    stessa chiamata aggiorna la riga, non ne crea un'altra. ``result`` e
    ``error_code`` sono valorizzati solo a esito raggiunto.
    """

    id: uuid.UUID
    run_id: uuid.UUID
    call_id: str
    tool_name: str
    arguments: Mapping[str, object]
    state: ToolInvocationState
    result: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", freeze_json(self.arguments))


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


#: Ragioni di terminazione esplicite (``finish_reason``). ``WORKER_LOST`` è
#: scritto anche da ``newray_reclaim_stale_runs`` (migrazione 0010) e
#: ``CANCELLED_BY_USER`` anche da ``PostgresRunStore.request_cancel`` e
#: dalla migrazione 0011: le stringhe devono restare identiche.
FINISH_REASON_EMPTY_OUTPUT = "empty_output"
FINISH_REASON_DEADLINE_EXCEEDED = "deadline_exceeded"
FINISH_REASON_MODEL_ERROR = "model_error"
FINISH_REASON_WORKER_LOST = "worker_lost"
FINISH_REASON_CANCELLED_BY_USER = "cancelled_by_user"
#: Superato il limite di turni tool per run (P-07, NewRay.md §8.1 passo 7):
#: la generazione si ferma con un esito esplicito, mai un ciclo infinito.
FINISH_REASON_TOOL_BUDGET_EXCEEDED = "tool_budget_exceeded"


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

    def list_events(self, scope: Scope, run_id: uuid.UUID, after_sequence: int) -> EventPage:
        """Eventi con ``sequence > after_sequence``, scoped come ``get``.

        ``EventPage.gap=True`` se eventi fra ``after_sequence`` e il primo
        restituito sono stati potati (retention dei soli ``message.delta``,
        migrazione 0011): il chiamante propone un ``resync``, non riprova
        a colmare il buco.
        """
        ...

    def request_cancel(self, scope: Scope, run_id: uuid.UUID) -> DurableRun | None:
        """Cancellazione persistita e idempotente (P-06, NewRay.md §8.3).

        Un run ``queued`` transita subito a ``cancelled`` (nessun worker lo
        possiede ancora, nessun fencing necessario). Un run ``running``
        riceve solo ``cancel_requested_at``: la transizione resta al
        worker, fencing-checked come ogni altro finalize. Un run già
        terminale non cambia stato (idempotente). ``None`` se il run non
        esiste o è fuori scope.
        """
        ...

    def find_active_by_conversation(
        self, scope: Scope, conversation_id: uuid.UUID
    ) -> DurableRun | None:
        """Run non terminale più recente della conversazione, scoped (P-06).

        Permette al client di ritrovare e riprendere lo stream di un run
        già in corso senza già possederne l'id — una nuova scheda o un
        refresh a metà generazione. ``None`` se non c'è alcun run
        queued/running per quella conversazione nello scope del principal
        (conversazione fuori scope inclusa: nessuna riga è visibile sotto
        RLS, non un errore distinto)."""
        ...

    def record_tool_event(
        self,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        invocation_id: uuid.UUID,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, object],
        state: ToolInvocationState,
        event_type: str,
        result: str | None,
        error_code: str | None,
    ) -> DurableRun | None:
        """Upsert idempotente della ricevuta ``tool_invocation`` e append di
        un evento tool nello stesso commit fencing-checked (P-07).

        Come heartbeat/finalize: la scrittura vale solo per il worker con il
        fence corrente sul run ``running``; ``None`` se il fencing è perso
        (un worker più recente possiede il run) — il chiamante interrompe il
        ciclo senza scrivere altro. La ricevuta è idempotente per
        ``(run_id, call_id)``: una transizione di stato aggiorna la riga.
        """
        ...

    def list_tool_invocations(self, scope: Scope, run_id: uuid.UUID) -> tuple[ToolInvocation, ...]:
        """Ricevute tool del run in ordine di creazione, scoped come ``get``
        (P-07). Vuoto se il run non esiste, è fuori scope o non ha tool."""
        ...
