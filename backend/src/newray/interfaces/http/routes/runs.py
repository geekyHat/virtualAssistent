"""Route dei run durevoli (P-05, P-06).

``POST /conversations/{id}/runs`` accoda un run idempotente (chiave
``Idempotency-Key``); nessuna generazione avviene qui: il worker in
background consuma la coda. ``GET /runs/{id}`` restituisce lo snapshot
autorevole dello stato. ``GET /runs/{id}/events`` è uno stream SSE
degli eventi durevoli con cursore ``after`` (query o header
``Last-Event-ID``), invalidato da revoca sessione. ``POST /runs/{id}/cancel``
è idempotente: il worker rileva la richiesta al prossimo checkpoint e
finalizza in ``CANCELLED``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from starlette.responses import StreamingResponse

from newray.interfaces.http.dto.runs import CreateRunRequest, RunSnapshotDTO
from newray.interfaces.http.errors import correlation_id
from newray.interfaces.http.middleware.identity import require_principal
from newray.interfaces.http.routes import API_PREFIX
from newray.kernel.errors import DomainError
from newray.kernel.identity import Principal
from newray.modules.runs import (
    TERMINAL_EVENT_TYPES,
    DurableRun,
    DurableRunService,
    RunEvent,
    RunEventReader,
)


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
        cancel_requested_at=run.cancel_requested_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _sse(event: RunEvent) -> str:
    """Envelope SSE versionato §19.3: id monotono, tipo, payload JSON."""
    body = {
        "event_id": str(event.id),
        "sequence": event.sequence,
        "type": event.event_type.value,
        "payload": dict(event.payload),
        "created_at": event.created_at.isoformat(),
    }
    return (
        f"id: {event.sequence}\n"
        f"event: {event.event_type.value}\n"
        f"data: {json.dumps(body, ensure_ascii=False, default=str)}\n\n"
    )


def _sse_error(code: str, message: str, correlation_id_value: str) -> str:
    body = {"code": code, "message": message, "correlation_id": correlation_id_value}
    return f"event: error\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"


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

    @router.post(
        "/runs/{run_id}/cancel",
        response_model=RunSnapshotDTO,
        summary="Richiede lo stop del run (idempotente)",
    )
    async def cancel_run(
        run_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> RunSnapshotDTO:
        service: DurableRunService = request.app.state.durable_run_service
        run = await service.cancel(principal, run_id)
        return _snapshot(run)

    @router.get(
        "/runs/{run_id}/events",
        summary="Stream SSE degli eventi del run con cursore",
    )
    async def stream_run_events(
        run_id: uuid.UUID,
        request: Request,
        after: Annotated[int, Query(ge=0, le=1_000_000_000)] = 0,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
        principal: Principal = Depends(require_principal),
    ) -> StreamingResponse:
        service: DurableRunService = request.app.state.durable_run_service
        reader: RunEventReader = request.app.state.run_event_reader
        cid = correlation_id(request)

        # Preferisce Last-Event-ID (Server-Sent Events reconnect) al query
        # ``after``: se entrambi sono presenti prevale il header.
        cursor = _cursor_from_header(last_event_id, default=after)

        # Autorizzazione: risolvere subito il run per lo scope; se non
        # esiste, 404 uniforme prima dello stream.
        run = await service.get(principal, run_id)

        async def event_stream() -> AsyncIterator[str]:
            current = cursor
            terminal_seen = False
            try:
                # Se la generazione è già terminata, mandiamo il replay
                # e chiudiamo. Altrimenti polliamo la outbox.
                while True:
                    if await request.is_disconnected():
                        return
                    batch = await asyncio.to_thread(
                        reader.list_events_after, principal.scope, run_id, current, 200
                    )
                    for event in batch:
                        yield _sse(event)
                        current = event.sequence
                        if event.event_type in TERMINAL_EVENT_TYPES:
                            terminal_seen = True
                    if terminal_seen:
                        return
                    # Se lo snapshot iniziale del run è già terminale ma
                    # non abbiamo ancora visto un evento terminale (es.
                    # cursore già oltre), non poll infinito: chiudi.
                    if (
                        run.state.value
                        in {
                            "completed",
                            "failed",
                            "cancelled",
                            "interrupted",
                        }
                        and not batch
                    ):
                        return
                    # Backoff moderato: la coda è breve e locale.
                    await asyncio.sleep(0.1)
            except DomainError as exc:
                yield _sse_error(exc.code, exc.message, cid)
            except Exception:
                yield _sse_error("INTERNAL", "errore interno del server", cid)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return router


def _cursor_from_header(last_event_id: str | None, *, default: int) -> int:
    if last_event_id is None:
        return default
    try:
        return max(int(last_event_id), 0)
    except ValueError:
        return default
