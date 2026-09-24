"""Persistenza P-05: ricevute e snapshot dei run sotto RLS FORCE."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, Row

from newray.kernel.errors import Conflict, NotFound
from newray.kernel.identity import Scope

from ..durable import DurableRun, EventPage, RunEvent, RunState, ToolInvocation, thaw_json
from ..tool_gateway import ToolInvocationState


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
        cancel_requested_at=row.cancel_requested_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _event(row: Row[Any]) -> RunEvent:
    return RunEvent(
        id=row.id,
        run_id=row.run_id,
        sequence=row.sequence,
        type=row.type,
        payload=dict(row.payload),
        occurred_at=row.occurred_at,
    )


_COLUMNS = (
    "id, conversation_id, organization_id, owner_id, idempotency_key, payload_hash, "
    "state, snapshot, partial_text, finish_reason, prompt_tokens, completion_tokens, "
    "eval_duration_ns, lease_owner, lease_until, fence, cancel_requested_at, "
    "created_at, updated_at"
)

_EVENT_COLUMNS = "id, run_id, sequence, type, payload, occurred_at"

_TOOL_COLUMNS = (
    "id, run_id, call_id, tool_name, arguments, state, result, error_code, created_at, updated_at"
)


def _tool_invocation(row: Row[Any]) -> ToolInvocation:
    return ToolInvocation(
        id=row.id,
        run_id=row.run_id,
        call_id=row.call_id,
        tool_name=row.tool_name,
        arguments=dict(row.arguments),
        state=ToolInvocationState(row.state),
        result=row.result,
        error_code=row.error_code,
        created_at=row.created_at,
        updated_at=row.updated_at,
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
                    "idempotency_key, payload_hash, state, snapshot, next_event_sequence, "
                    "created_at, updated_at) "
                    "VALUES (:id, :conversation_id, :organization_id, :owner_id, "
                    ":idempotency_key, :payload_hash, 'queued', CAST(:snapshot AS jsonb), 1, "
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
                conn.execute(
                    text(
                        "INSERT INTO run_events (id, run_id, organization_id, owner_id, "
                        "sequence, type, payload, occurred_at) VALUES "
                        "(gen_random_uuid(), :run_id, :organization_id, :owner_id, 1, "
                        "'run.queued', '{}'::jsonb, :occurred_at)"
                    ),
                    {
                        "run_id": run.id,
                        "organization_id": run.organization_id,
                        "owner_id": run.owner_id,
                        "occurred_at": run.created_at,
                    },
                )
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

    def find_active_by_conversation(
        self, scope: Scope, conversation_id: uuid.UUID
    ) -> DurableRun | None:
        with self._engine.connect() as conn:
            _scope(conn, scope)
            row = conn.execute(
                text(
                    "SELECT " + _COLUMNS + " FROM runs WHERE conversation_id = :conversation_id "
                    "AND state IN ('queued', 'running') "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"conversation_id": conversation_id},
            ).first()
            return None if row is None else _run(row)

    def count_active(self, scope: Scope) -> int:
        with self._engine.connect() as conn:
            _scope(conn, scope)
            count = conn.execute(
                text("SELECT count(*) FROM runs WHERE state IN ('queued', 'running')")
            ).scalar_one()
            return int(count)

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
        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT " + _COLUMNS + " FROM newray_record_tool_event("
                    ":run_id, :worker, :fence, :invocation_id, :call_id, :tool_name, "
                    "CAST(:arguments AS jsonb), :state, :event_type, :result, :error_code)"
                ),
                {
                    "run_id": run_id,
                    "worker": worker_id,
                    "fence": fence,
                    "invocation_id": invocation_id,
                    "call_id": call_id,
                    "tool_name": tool_name,
                    "arguments": json.dumps(thaw_json(arguments), ensure_ascii=False),
                    "state": str(state),
                    "event_type": event_type,
                    "result": result,
                    "error_code": error_code,
                },
            ).first()
            return None if row is None or row.id is None else _run(row)

    def list_tool_invocations(self, scope: Scope, run_id: uuid.UUID) -> tuple[ToolInvocation, ...]:
        with self._engine.connect() as conn:
            _scope(conn, scope)
            rows = conn.execute(
                text(
                    "SELECT " + _TOOL_COLUMNS + " FROM tool_invocations "
                    "WHERE run_id = :run_id ORDER BY created_at, call_id"
                ),
                {"run_id": run_id},
            ).all()
            return tuple(_tool_invocation(row) for row in rows)

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

    def list_events(self, scope: Scope, run_id: uuid.UUID, after_sequence: int) -> EventPage:
        with self._engine.connect() as conn:
            _scope(conn, scope)
            rows = conn.execute(
                text(
                    "SELECT " + _EVENT_COLUMNS + " FROM run_events "
                    "WHERE run_id = :run_id AND sequence > :after_sequence ORDER BY sequence"
                ),
                {"run_id": run_id, "after_sequence": after_sequence},
            ).all()
            latest = conn.execute(
                text("SELECT COALESCE(MAX(sequence), 0) FROM run_events WHERE run_id = :run_id"),
                {"run_id": run_id},
            ).scalar_one()
            # Buco: il primo evento restituito non è il successore diretto
            # del cursore richiesto → qualcosa fra i due è stato potato
            # (retention dei soli message.delta, migrazione 0011).
            gap = bool(rows) and rows[0].sequence != after_sequence + 1
            return EventPage(
                events=tuple(_event(row) for row in rows), gap=gap, latest_sequence=int(latest)
            )

    def request_cancel(self, scope: Scope, run_id: uuid.UUID) -> DurableRun | None:
        with self._engine.begin() as conn:
            _scope(conn, scope)
            row = conn.execute(
                text(
                    "UPDATE runs SET "
                    "cancel_requested_at = COALESCE(cancel_requested_at, now()), "
                    "state = CASE WHEN state = 'queued' THEN 'cancelled' ELSE state END, "
                    "finish_reason = CASE WHEN state = 'queued' THEN 'cancelled_by_user' "
                    "ELSE finish_reason END, "
                    "next_event_sequence = CASE WHEN state = 'queued' "
                    "THEN next_event_sequence + 1 ELSE next_event_sequence END, "
                    "updated_at = now() "
                    "WHERE id = :run_id AND state IN ('queued', 'running') "
                    "RETURNING " + _COLUMNS + ", next_event_sequence"
                ),
                {"run_id": run_id},
            ).first()
            if row is None:
                existing = conn.execute(
                    text("SELECT " + _COLUMNS + " FROM runs WHERE id = :run_id"),
                    {"run_id": run_id},
                ).first()
                return None if existing is None else _run(existing)

            if row.state == "cancelled":
                conn.execute(
                    text(
                        "INSERT INTO run_events (id, run_id, organization_id, owner_id, "
                        "sequence, type, payload, occurred_at) VALUES "
                        "(gen_random_uuid(), :run_id, :organization_id, :owner_id, "
                        ":sequence, 'run.cancelled', "
                        "jsonb_build_object('finish_reason', 'cancelled_by_user', 'text', '', "
                        "'prompt_tokens', NULL, 'completion_tokens', NULL, "
                        "'eval_duration_ns', NULL), now())"
                    ),
                    {
                        "run_id": run_id,
                        "organization_id": row.organization_id,
                        "owner_id": row.owner_id,
                        "sequence": row.next_event_sequence,
                    },
                )
            return _run(row)
