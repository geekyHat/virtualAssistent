"""Route dei run durevoli (P-05, NewRay.md §19.3).

Solo creazione idempotente e lettura dello snapshot: cancellazione ed
eventi/stream restano P-06. Fuori scope → 404 uniforme (§7.3), come le
altre risorse private.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request

from newray.interfaces.http.dto.runs import CreateRunRequest, RunDTO
from newray.interfaces.http.middleware.identity import require_principal
from newray.interfaces.http.routes import API_PREFIX
from newray.kernel.identity import Principal
from newray.modules.runs import DurableRun, DurableRunService


def _run(run: DurableRun) -> RunDTO:
    return RunDTO(
        id=run.id,
        conversation_id=run.conversation_id,
        state=run.state,
        partial_text=run.partial_text,
        finish_reason=run.finish_reason,
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        eval_duration_ns=run.eval_duration_ns,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def build_router() -> APIRouter:
    """Costruisce le route dei run; il servizio viene da ``app.state``."""
    router = APIRouter(prefix=API_PREFIX)

    @router.post("/conversations/{conversation_id}/runs", status_code=201, response_model=RunDTO)
    async def create_run(
        conversation_id: uuid.UUID,
        request: Request,
        body: CreateRunRequest,
        principal: Principal = Depends(require_principal),
    ) -> RunDTO:
        """Accoda un run durevole; il worker lo consuma in background (P-05)."""
        service: DurableRunService = request.app.state.durable_run_service
        run = await service.create(
            principal,
            conversation_id,
            body.profile_id,
            body.content,
            body.idempotency_key,
        )
        return _run(run)

    @router.get("/runs/{run_id}", response_model=RunDTO)
    async def get_run(
        run_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> RunDTO:
        """Snapshot del run; fuori scope → 404 (§7.3)."""
        service: DurableRunService = request.app.state.durable_run_service
        run = await service.get(principal, run_id)
        return _run(run)

    return router
