"""Persistenza P-05: ricevute, snapshot e ciclo di vita worker sotto RLS FORCE."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, Row

from newray.kernel.errors import Conflict, NotFound, QueueFull
from newray.kernel.identity import Scope

from ..durable import (
    TERMINAL_STATES,
    ClaimedRun,
    DurableRun,
    LeaseLost,
    RunAlreadyTerminal,
    RunState,
    thaw_json,
)

__all__ = ["LeaseLost", "PostgresRunStore", "RunAlreadyTerminal"]


def _scope(conn: Connection, scope: Scope) -> None:
    conn.execute(text("SELECT set_config('app.user_id', :v, true)"), {"v": str(scope.user_id)})
    conn.execute(
        text("SELECT set_config('app.organization_id', :v, true)"),
        {"v": str(scope.organization_id)},
    )


def _run(row: Row[Any]) -> DurableRun:
    return DurableRun(
        id=row.id,
        conversation_id=row.conversation_id,
        organization_id=row.organization_id,
        owner_id=row.owner_id,
        idempotency_key=row.idempotency_key,
        payload_hash=row.payload_hash,
        state=RunState(row.state),
        snapshot=dict(row.snapshot),
        partial_text=row.partial_text,
        finish_reason=row.finish_reason,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        eval_duration_ns=row.eval_duration_ns,
        lease_owner=row.lease_owner,
        lease_until=row.lease_until,
        fence=row.fence,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deadline_at=row.deadline_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        cancel_requested_at=row.cancel_requested_at,
        heartbeat_at=row.heartbeat_at,
        error_code=row.error_code,
    )


_COLUMNS = (
    "id, conversation_id, organization_id, owner_id, idempotency_key, payload_hash, "
    "state, snapshot, partial_text, finish_reason, prompt_tokens, completion_tokens, "
    "eval_duration_ns, lease_owner, lease_until, fence, created_at, updated_at, "
    "deadline_at, started_at, finished_at, cancel_requested_at, heartbeat_at, error_code"
)


class PostgresRunStore:
    """Run store con barriera UNIQUE, lock conversazione, scope per transazione
    e claim/fencing atomico per il worker durevole.

    Il worker non appartiene a un principal: `claim_next` usa la funzione
    SECURITY DEFINER `runs_claim_next` per bypassare RLS solo per la scelta
    atomica del prossimo run. Ogni altra operazione posiziona esplicitamente
    lo scope del run e viaggia sotto RLS FORCE.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # -- creazione ------------------------------------------------------

    def enqueue(self, run: DurableRun, *, queue_cap: int | None = None) -> DurableRun:
        scope = Scope(run.organization_id, run.owner_id)
        with self._engine.begin() as conn:
            _scope(conn, scope)
            # Serializza i create concorrenti per lo stesso owner: due
            # transazioni al bordo del cap non possono entrambe inserire.
            # L'hash da 64 bit collide con probabilità trascurabile per lo
            # spazio uuid×uuid; una collisione degrada a serializzazione più
            # ampia, mai a violazione del cap.
            conn.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(CAST(:org AS text) || ':' "
                    "|| CAST(:owner AS text), 0))"
                ),
                {"org": run.organization_id, "owner": run.owner_id},
            )
            parent = conn.execute(
                text("SELECT id FROM conversations WHERE id = :id FOR UPDATE"),
                {"id": run.conversation_id},
            ).first()
            if parent is None:
                raise NotFound("conversazione non trovata")
            # Replay idempotente ha precedenza sul cap: chi ritenta con la
            # stessa chiave riceve la sua ricevuta, non un rifiuto artificiale.
            existing_by_key = conn.execute(
                text(
                    "SELECT " + _COLUMNS + " FROM runs WHERE conversation_id = :conversation_id "
                    "AND idempotency_key = :idempotency_key"
                ),
                {
                    "conversation_id": run.conversation_id,
                    "idempotency_key": run.idempotency_key,
                },
            ).first()
            if existing_by_key is not None:
                if existing_by_key.payload_hash != run.payload_hash:
                    raise Conflict("chiave di idempotenza riutilizzata con richiesta diversa")
                return _run(existing_by_key)
            if queue_cap is not None:
                pending = conn.execute(
                    text(
                        "SELECT count(*) FROM runs "
                        "WHERE state NOT IN "
                        "('completed', 'failed', 'cancelled', 'interrupted')"
                    )
                ).scalar_one()
                if pending >= queue_cap:
                    raise QueueFull(
                        f"coda piena ({pending}/{queue_cap} run non-terminali)"
                    )
            inserted = conn.execute(
                text(
                    "INSERT INTO runs (id, conversation_id, organization_id, owner_id, "
                    "idempotency_key, payload_hash, state, snapshot, deadline_at, "
                    "created_at, updated_at) "
                    "VALUES (:id, :conversation_id, :organization_id, :owner_id, "
                    ":idempotency_key, :payload_hash, 'queued', CAST(:snapshot AS jsonb), "
                    ":deadline_at, :created_at, :updated_at) "
                    "ON CONFLICT ON CONSTRAINT runs_scope_key DO NOTHING "
                    "RETURNING " + _COLUMNS
                ),
                {
                    "id": run.id,
                    "conversation_id": run.conversation_id,
                    "organization_id": run.organization_id,
                    "owner_id": run.owner_id,
                    "idempotency_key": run.idempotency_key,
                    "payload_hash": run.payload_hash,
                    "snapshot": json.dumps(
                        thaw_json(run.snapshot), ensure_ascii=False, sort_keys=True
                    ),
                    "deadline_at": run.deadline_at,
                    "created_at": run.created_at,
                    "updated_at": run.updated_at,
                },
            ).first()
            if inserted is None:
                # Impossibile sotto l'advisory lock: la SELECT precedente
                # avrebbe già intercettato l'esistenza. Se accade è un
                # errore invariante da segnalare, non da silenziare.
                raise RuntimeError("run persistito da una transazione concorrente")
            return _run(inserted)

    # -- letture --------------------------------------------------------

    def get(self, scope: Scope, run_id: uuid.UUID) -> DurableRun | None:
        with self._engine.connect() as conn:
            _scope(conn, scope)
            row = conn.execute(
                text("SELECT " + _COLUMNS + " FROM runs WHERE id = :id"),
                {"id": run_id},
            ).first()
            return None if row is None else _run(row)

    def find_by_key(
        self, scope: Scope, conversation_id: uuid.UUID, idempotency_key: str
    ) -> DurableRun | None:
        with self._engine.connect() as conn:
            _scope(conn, scope)
            row = conn.execute(
                text(
                    "SELECT " + _COLUMNS + " FROM runs WHERE conversation_id = :conversation_id "
                    "AND idempotency_key = :idempotency_key"
                ),
                {"conversation_id": conversation_id, "idempotency_key": idempotency_key},
            ).first()
            return None if row is None else _run(row)

    # -- ciclo di vita worker ------------------------------------------

    def claim_next(
        self,
        *,
        worker_id: uuid.UUID,
        lease_until: datetime,
        now: datetime,
    ) -> ClaimedRun | None:
        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT " + _COLUMNS + " FROM runs_claim_next(:worker, :lease, :now)"
                ),
                {"worker": worker_id, "lease": lease_until, "now": now},
            ).first()
            if row is None:
                return None
            claimed = _run(row)
            return ClaimedRun(run=claimed, fence=claimed.fence)

    def heartbeat(
        self,
        *,
        scope: Scope,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        lease_until: datetime,
        now: datetime,
    ) -> DurableRun:
        return self._advance_lease(
            scope=scope,
            run_id=run_id,
            worker_id=worker_id,
            fence=fence,
            lease_until=lease_until,
            now=now,
            partial_text=None,
        )

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
    ) -> DurableRun:
        return self._advance_lease(
            scope=scope,
            run_id=run_id,
            worker_id=worker_id,
            fence=fence,
            lease_until=lease_until,
            now=now,
            partial_text=partial_text,
        )

    def _advance_lease(
        self,
        *,
        scope: Scope,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        lease_until: datetime,
        now: datetime,
        partial_text: str | None,
    ) -> DurableRun:
        with self._engine.begin() as conn:
            _scope(conn, scope)
            self._require_owned(conn, run_id, worker_id, fence)
            if partial_text is None:
                updated = conn.execute(
                    text(
                        "UPDATE runs SET lease_until = :lease_until, heartbeat_at = :now, "
                        "updated_at = :now WHERE id = :id RETURNING " + _COLUMNS
                    ),
                    {"lease_until": lease_until, "now": now, "id": run_id},
                ).one()
            else:
                updated = conn.execute(
                    text(
                        "UPDATE runs SET partial_text = :partial_text, "
                        "lease_until = :lease_until, heartbeat_at = :now, "
                        "updated_at = :now WHERE id = :id RETURNING " + _COLUMNS
                    ),
                    {
                        "partial_text": partial_text,
                        "lease_until": lease_until,
                        "now": now,
                        "id": run_id,
                    },
                ).one()
            return _run(updated)

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
    ) -> DurableRun:
        with self._engine.begin() as conn:
            _scope(conn, scope)
            self._require_owned(conn, run_id, worker_id, fence)
            updated = conn.execute(
                text(
                    "UPDATE runs SET state = 'completed', partial_text = :partial_text, "
                    "finish_reason = :finish_reason, prompt_tokens = :prompt_tokens, "
                    "completion_tokens = :completion_tokens, eval_duration_ns = :eval_duration_ns, "
                    "lease_owner = NULL, lease_until = NULL, finished_at = :now, "
                    "heartbeat_at = :now, updated_at = :now WHERE id = :id RETURNING " + _COLUMNS
                ),
                {
                    "partial_text": partial_text,
                    "finish_reason": finish_reason,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "eval_duration_ns": eval_duration_ns,
                    "now": now,
                    "id": run_id,
                },
            ).one()
            return _run(updated)

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
    ) -> DurableRun:
        with self._engine.begin() as conn:
            _scope(conn, scope)
            self._require_owned(conn, run_id, worker_id, fence)
            updated = conn.execute(
                text(
                    "UPDATE runs SET state = 'failed', partial_text = :partial_text, "
                    "finish_reason = :finish_reason, error_code = :error_code, "
                    "lease_owner = NULL, lease_until = NULL, finished_at = :now, "
                    "heartbeat_at = :now, updated_at = :now WHERE id = :id RETURNING " + _COLUMNS
                ),
                {
                    "partial_text": partial_text,
                    "finish_reason": finish_reason,
                    "error_code": error_code,
                    "now": now,
                    "id": run_id,
                },
            ).one()
            return _run(updated)

    def finalize_cancelled(
        self,
        *,
        scope: Scope,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        partial_text: str,
        now: datetime,
    ) -> DurableRun:
        with self._engine.begin() as conn:
            _scope(conn, scope)
            self._require_owned(conn, run_id, worker_id, fence)
            updated = conn.execute(
                text(
                    "UPDATE runs SET state = 'cancelled', partial_text = :partial_text, "
                    "finish_reason = 'cancelled', lease_owner = NULL, lease_until = NULL, "
                    "finished_at = :now, heartbeat_at = :now, updated_at = :now "
                    "WHERE id = :id RETURNING " + _COLUMNS
                ),
                {"partial_text": partial_text, "now": now, "id": run_id},
            ).one()
            return _run(updated)

    def interrupt_stale(
        self,
        *,
        scope: Scope,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        now: datetime,
    ) -> DurableRun:
        with self._engine.begin() as conn:
            _scope(conn, scope)
            self._require_owned(conn, run_id, worker_id, fence)
            updated = conn.execute(
                text(
                    "UPDATE runs SET state = 'interrupted', finish_reason = 'interrupted', "
                    "lease_owner = NULL, lease_until = NULL, finished_at = :now, "
                    "heartbeat_at = :now, updated_at = :now WHERE id = :id "
                    "RETURNING " + _COLUMNS
                ),
                {"now": now, "id": run_id},
            ).one()
            return _run(updated)

    def mark_cancel_requested(
        self, scope: Scope, run_id: uuid.UUID, now: datetime
    ) -> DurableRun:
        with self._engine.begin() as conn:
            _scope(conn, scope)
            row = conn.execute(
                text("SELECT " + _COLUMNS + " FROM runs WHERE id = :id FOR UPDATE"),
                {"id": run_id},
            ).first()
            if row is None:
                raise NotFound("run non trovato")
            if RunState(row.state) in TERMINAL_STATES:
                return _run(row)
            if row.cancel_requested_at is not None:
                return _run(row)
            updated = conn.execute(
                text(
                    "UPDATE runs SET cancel_requested_at = :now, updated_at = :now "
                    "WHERE id = :id RETURNING " + _COLUMNS
                ),
                {"now": now, "id": run_id},
            ).one()
            return _run(updated)

    # -- helper --------------------------------------------------------

    def _require_owned(
        self, conn: Connection, run_id: uuid.UUID, worker_id: uuid.UUID, fence: int
    ) -> None:
        row = conn.execute(
            text(
                "SELECT state, lease_owner, fence FROM runs WHERE id = :id FOR UPDATE"
            ),
            {"id": run_id},
        ).first()
        if row is None:
            raise LeaseLost("run non visibile sotto scope")
        if RunState(row.state) in TERMINAL_STATES:
            raise RunAlreadyTerminal("run già terminato")
        if row.lease_owner != worker_id or row.fence != fence:
            raise LeaseLost("lease non più valido")
