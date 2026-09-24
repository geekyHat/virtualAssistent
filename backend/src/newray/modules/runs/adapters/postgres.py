"""Persistenza P-05: ricevute e snapshot dei run sotto RLS FORCE."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, Row

from newray.kernel.errors import Conflict, NotFound
from newray.kernel.identity import Scope

from ..durable import DurableRun, RunState, thaw_json


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

    def count_active(self, scope: Scope) -> int:
        with self._engine.connect() as conn:
            _scope(conn, scope)
            count = conn.execute(
                text("SELECT count(*) FROM runs WHERE state IN ('queued', 'running')")
            ).scalar_one()
            return int(count)

    def claim(
        self, worker_id: uuid.UUID, lease_seconds: int, resource_id: str
    ) -> DurableRun | None:
        with self._engine.begin() as conn:
            row = conn.execute(
                text("SELECT " + _COLUMNS + " FROM newray_claim_run(:worker, :lease, :resource)"),
                {"worker": worker_id, "lease": lease_seconds, "resource": resource_id},
            ).first()
            return None if row is None or row.id is None else _run(row)

    def heartbeat(
        self,
        run_id: uuid.UUID,
        worker_id: uuid.UUID,
        fence: int,
        lease_seconds: int,
        partial_text: str,
        resource_id: str,
    ) -> DurableRun | None:
        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT " + _COLUMNS + " FROM newray_heartbeat_run("
                    ":run_id, :worker, :fence, :lease, :partial_text, :resource)"
                ),
                {
                    "run_id": run_id,
                    "worker": worker_id,
                    "fence": fence,
                    "lease": lease_seconds,
                    "partial_text": partial_text,
                    "resource": resource_id,
                },
            ).first()
            return None if row is None or row.id is None else _run(row)

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
        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT " + _COLUMNS + " FROM newray_finalize_run("
                    ":run_id, :worker, :fence, :state, :finish_reason, :prompt_tokens, "
                    ":completion_tokens, :eval_duration_ns, :partial_text, :resource)"
                ),
                {
                    "run_id": run_id,
                    "worker": worker_id,
                    "fence": fence,
                    "state": str(state),
                    "finish_reason": finish_reason,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "eval_duration_ns": eval_duration_ns,
                    "partial_text": partial_text,
                    "resource": resource_id,
                },
            ).first()
            return None if row is None or row.id is None else _run(row)

    def reclaim_stale(self, grace_seconds: int) -> tuple[DurableRun, ...]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                text("SELECT " + _COLUMNS + " FROM newray_reclaim_stale_runs(:grace)"),
                {"grace": grace_seconds},
            ).all()
            return tuple(_run(row) for row in rows)

    def register_worker(self, worker_id: uuid.UUID, pid: int, hostname: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO run_workers (worker_id, pid, hostname, started_at, "
                    "last_heartbeat_at) VALUES (:worker_id, :pid, :hostname, now(), now()) "
                    "ON CONFLICT (worker_id) DO UPDATE SET pid = EXCLUDED.pid, "
                    "hostname = EXCLUDED.hostname, started_at = now(), "
                    "last_heartbeat_at = now()"
                ),
                {"worker_id": worker_id, "pid": pid, "hostname": hostname},
            )

    def touch_worker(self, worker_id: uuid.UUID) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE run_workers SET last_heartbeat_at = now() WHERE worker_id = :worker_id"
                ),
                {"worker_id": worker_id},
            )
