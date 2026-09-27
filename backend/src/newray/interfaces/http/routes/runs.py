"""Route dei run durevoli (P-05).

Il caso d'uso appartiene al modulo ``runs``. Questa route sostituisce la
preview inline ``/conversations/{id}/run`` per i client nuovi; la vecchia
resta come compatibilità finché la migrazione della chat non è chiusa
(P-06). Gli eventi/streaming sono responsabilità di P-06 e non sono qui.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status

from newray.interfaces.http.dto.runs import CreateRunRequest, RunSnapshotDTO
from newray.interfaces.http.middleware.identity import require_principal
from newray.interfaces.http.routes import API_PREFIX
from newray.kernel.identity import Principal
from newray.modules.runs import DurableRun, DurableRunService


def _int(value: object) -> int:
    if isinstance(value, bool | int):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError(f"valore non intero nello snapshot: {value!r}")


def _snapshot(run: DurableRun) -> RunSnapshotDTO:
    s = run.snapshot
    return RunSnapshotDTO(
        id=run.id,
        conversation_id=run.conversation_id,
        state=run.state,
        idempotency_key=run.idempotency_key,
        partial_text=run.partial_text,
        finish_reason=run.finish_reason,
        error_code=run.error_code,
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        eval_duration_ns=run.eval_duration_ns,
        model_name=str(s.get("model_name", "")),
        digest=(str(s["digest"]) if s.get("digest") else None),
        profile_id=uuid.UUID(str(s["profile_id"])),
        profile_version_id=uuid.UUID(str(s["profile_version_id"])),
        binding_id=uuid.UUID(str(s["binding_id"])),
        context_message_count=_int(s.get("context_message_count", 0)),
        context_character_count=_int(s.get("context_character_count", 0)),
        context_truncated=bool(s.get("context_truncated", False)),
        max_output_tokens=(
            _int(s["max_output_tokens"]) if s.get("max_output_tokens") is not None else None
        ),
        created_at=run.created_at,
        updated_at=run.updated_at,
        deadline_at=run.deadline_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        cancel_requested_at=run.cancel_requested_at,
        heartbeat_at=run.heartbeat_at,
        fence=run.fence,
    )


def build_router() -> APIRouter:
    router = APIRouter(prefix=API_PREFIX)

    @router.post(
        "/conversations/{conversation_id}/runs",
        response_model=RunSnapshotDTO,
        status_code=status.HTTP_201_CREATED,
        summary="Accoda un run durevole (worker lo esegue asincronamente)",
    )
    async def create_run(
        conversation_id: uuid.UUID,
        body: CreateRunRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> RunSnapshotDTO:
        service: DurableRunService | None = getattr(
            request.app.state, "durable_run_service", None
        )
        if service is None:
            raise HTTPException(status_code=503, detail="durable runs non disponibili")
        run = await service.create(
            principal,
            conversation_id,
            body.profile_id,
            body.content,
            body.idempotency_key,
        )
        return _snapshot(run)

    @router.get(
        "/runs/{run_id}",
        response_model=RunSnapshotDTO,
        summary="Snapshot corrente di un run durevole",
    )
    async def get_run(
        run_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> RunSnapshotDTO:
        service: DurableRunService | None = getattr(
            request.app.state, "durable_run_service", None
        )
        if service is None:
            raise HTTPException(status_code=503, detail="durable runs non disponibili")
        run = await service.get(principal, run_id)
        return _snapshot(run)

    @router.post(
        "/runs/{run_id}/cancel",
        response_model=RunSnapshotDTO,
        summary="Richiesta idempotente di cancellazione; il worker onora al primo controllo",
    )
    async def cancel_run(
        run_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> RunSnapshotDTO:
        service: DurableRunService | None = getattr(
            request.app.state, "durable_run_service", None
        )
        if service is None:
            raise HTTPException(status_code=503, detail="durable runs non disponibili")
        run = await service.request_cancel(principal, run_id)
        return _snapshot(run)

    return router
