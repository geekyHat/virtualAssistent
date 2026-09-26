"""Route dei run durevoli (P-05).

``POST /conversations/{id}/runs`` accoda un run idempotente (chiave
``Idempotency-Key``); nessuna generazione avviene qui: il worker in
background consuma la coda. ``GET /runs/{id}`` restituisce lo snapshot
autorevole dello stato; gli eventi in streaming sono di P-06.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response

from newray.interfaces.http.dto.runs import CreateRunRequest, RunSnapshotDTO
from newray.interfaces.http.middleware.identity import require_principal
from newray.interfaces.http.routes import API_PREFIX
from newray.kernel.identity import Principal
from newray.modules.runs import DurableRun, DurableRunService


def _snapshot(run: DurableRun) -> RunSnapshotDTO:
    """Proietta il run in un DTO stabile per il client."""
    snapshot = dict(run.snapshot)
    model_name_raw = snapshot.get("model_name")
    digest_raw = snapshot.get("digest")
    truncated_raw = snapshot.get("context_truncated")
    return RunSnapshotDTO(
        id=run.id,
        conversation_id=run.conversation_id,
        state=str(run.state.value),
        finish_reason=run.finish_reason,
        partial_text=run.partial_text,
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        eval_duration_ns=run.eval_duration_ns,
        model_name=model_name_raw if isinstance(model_name_raw, str) else None,
        digest=digest_raw if isinstance(digest_raw, str) else None,
        context_truncated=truncated_raw if isinstance(truncated_raw, bool) else None,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def build_router() -> APIRouter:
    router = APIRouter(prefix=API_PREFIX)

    @router.post(
        "/conversations/{conversation_id}/runs",
        status_code=201,
        response_model=RunSnapshotDTO,
        summary="Accoda un run durevole (idempotente)",
    )
    async def create_run(
        conversation_id: uuid.UUID,
        request: Request,
        body: CreateRunRequest,
        response: Response,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=96),
        ],
        principal: Principal = Depends(require_principal),
    ) -> RunSnapshotDTO:
        service: DurableRunService = request.app.state.durable_run_service
        run = await service.create(
            principal,
            conversation_id,
            body.profile_id,
            body.content,
            idempotency_key,
        )
        response.headers["Location"] = f"{API_PREFIX}/runs/{run.id}"
        return _snapshot(run)

    @router.get(
        "/runs/{run_id}",
        response_model=RunSnapshotDTO,
        summary="Snapshot autorevole dello stato del run",
    )
    async def get_run(
        run_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> RunSnapshotDTO:
        service: DurableRunService = request.app.state.durable_run_service
        run = await service.get(principal, run_id)
        return _snapshot(run)

    return router
