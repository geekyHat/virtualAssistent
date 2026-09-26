"""Persistenza P-05: ricevute e snapshot dei run sotto RLS FORCE."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, Row

from newray.kernel.errors import Conflict, NotFound
from newray.kernel.identity import Scope

from ..durable import ClaimedRun, DurableRun, RunState, thaw_json


def _scope(conn: Connection, scope: Scope) -> None:
    conn.execute(text("SELECT set_config('app.user_id', :v, true)"), {"v": str(scope.user_id)})
    conn.execute(
        text("SELECT set_config('app.organization_id', :v, true)"),
        {"v": str(scope.organization_id)},
    )


def _worker(conn: Connection, worker_id: uuid.UUID) -> None:
    """Attiva la policy ``runs_worker_access`` per questa transazione.

    La variabile ``app.worker_id`` è locale alla connessione e va impostata
    solo dai processi di scheduler; le route utente non la toccano.
    """
    conn.execute(
        text("SELECT set_config('app.worker_id', :v, true)"),
        {"v": str(worker_id)},
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
    )


_COLUMNS = (
    "id, conversation_id, organization_id, owner_id, idempotency_key, payload_hash, "
    "state, snapshot, partial_text, finish_reason, prompt_tokens, completion_tokens, "
    "eval_duration_ns, lease_owner, lease_until, fence, created_at, updated_at"
)


class PostgresRunStore:
    """Run store con barriera UNIQUE, lock conversazione e scope per transazione."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def enqueue(self, run: DurableRun) -> DurableRun:
        scope = Scope(run.organization_id, run.owner_id)
        with self._engine.begin() as conn:
            _scope(conn, scope)
            # Serializza creazione e delete della conversazione, senza
            # mantenere il lock durante inference.
            parent = conn.execute(
                text("SELECT id FROM conversations WHERE id = :id FOR UPDATE"),
                {"id": run.conversation_id},
            ).first()
            if parent is None:
                raise NotFound("conversazione non trovata")
            inserted = conn.execute(
                text(
                    "INSERT INTO runs (id, conversation_id, organization_id, owner_id, "
                    "idempotency_key, payload_hash, state, snapshot, created_at, updated_at) "
                    "VALUES (:id, :conversation_id, :organization_id, :owner_id, "
                    ":idempotency_key, :payload_hash, 'queued', CAST(:snapshot AS jsonb), "
                    ":created_at, :updated_at) "
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
                    "created_at": run.created_at,
                    "updated_at": run.updated_at,
                },
            ).first()
            if inserted is not None:
                return _run(inserted)
            existing = conn.execute(
                text(
                    "SELECT " + _COLUMNS + " FROM runs WHERE conversation_id = :conversation_id "
                    "AND idempotency_key = :idempotency_key"
                ),
                {"conversation_id": run.conversation_id, "idempotency_key": run.idempotency_key},
            ).one()
            if existing.payload_hash != run.payload_hash:
                raise Conflict("chiave di idempotenza riutilizzata con richiesta diversa")
            return _run(existing)

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

    def claim(
        self,
        worker_id: uuid.UUID,
        lease_duration_seconds: int,
        now: datetime,
    ) -> ClaimedRun | None:
        if lease_duration_seconds <= 0:
            raise ValueError("lease_duration_seconds deve essere > 0")
        lease_until = _add_seconds(now, lease_duration_seconds)
        with self._engine.begin() as conn:
            _worker(conn, worker_id)
            row = conn.execute(
                text(
                    "UPDATE runs SET "
                    "state = 'running', "
                    "lease_owner = :worker, "
                    "lease_until = :lease_until, "
                    "fence = fence + 1, "
                    "updated_at = :now "
                    "WHERE id = ("
                    "  SELECT id FROM runs "
                    "  WHERE state = 'queued' "
                    "     OR (state = 'running' "
                    "         AND lease_until IS NOT NULL "
                    "         AND lease_until < :now) "
                    "  ORDER BY created_at "
                    "  FOR UPDATE SKIP LOCKED "
                    "  LIMIT 1"
                    ") "
                    "RETURNING " + _COLUMNS
                ),
                {"worker": worker_id, "lease_until": lease_until, "now": now},
            ).first()
            if row is None:
                return None
            run = _run(row)
            # Il lease_until del row è quello appena scritto: normalizzarne
            # la timezone lato Python è responsabilità dell'adapter DB.
            assert run.lease_until is not None
            assert run.fence >= 1
            return ClaimedRun(
                run=run,
                worker_id=worker_id,
                lease_until=run.lease_until,
                fence=run.fence,
            )

    def renew_lease(
        self,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        lease_until: datetime,
        now: datetime,
    ) -> bool:
        with self._engine.begin() as conn:
            _worker(conn, worker_id)
            result = conn.execute(
                text(
                    "UPDATE runs SET lease_until = :lease_until, updated_at = :now "
                    "WHERE id = :id AND lease_owner = :worker "
                    "AND fence = :fence AND state = 'running'"
                ),
                {
                    "id": run_id,
                    "worker": worker_id,
                    "fence": fence,
                    "lease_until": lease_until,
                    "now": now,
                },
            )
            return result.rowcount == 1

    def save_partial(
        self,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        partial_text: str,
        now: datetime,
    ) -> bool:
        with self._engine.begin() as conn:
            _worker(conn, worker_id)
            result = conn.execute(
                text(
                    "UPDATE runs SET partial_text = :partial, updated_at = :now "
                    "WHERE id = :id AND lease_owner = :worker "
                    "AND fence = :fence AND state = 'running'"
                ),
                {
                    "id": run_id,
                    "worker": worker_id,
                    "fence": fence,
                    "partial": partial_text,
                    "now": now,
                },
            )
            return result.rowcount == 1

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
        if state not in _TERMINAL_STATES:
            raise ValueError(f"stato terminale non ammesso: {state}")
        with self._engine.begin() as conn:
            _worker(conn, worker_id)
            result = conn.execute(
                text(
                    "UPDATE runs SET "
                    "state = :state, "
                    "finish_reason = :finish_reason, "
                    "partial_text = :partial, "
                    "prompt_tokens = :prompt_tokens, "
                    "completion_tokens = :completion_tokens, "
                    "eval_duration_ns = :eval_duration_ns, "
                    "lease_owner = NULL, "
                    "lease_until = NULL, "
                    "updated_at = :now "
                    "WHERE id = :id AND lease_owner = :worker "
                    "AND fence = :fence AND state = 'running'"
                ),
                {
                    "id": run_id,
                    "worker": worker_id,
                    "fence": fence,
                    "state": state.value,
                    "finish_reason": finish_reason,
                    "partial": partial_text,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "eval_duration_ns": eval_duration_ns,
                    "now": now,
                },
            )
            return result.rowcount == 1


_TERMINAL_STATES = frozenset(
    {
        RunState.COMPLETED,
        RunState.FAILED,
        RunState.CANCELLED,
        RunState.INTERRUPTED,
    }
)


def _add_seconds(now: datetime, seconds: int) -> datetime:
    from datetime import timedelta

    return now + timedelta(seconds=seconds)
