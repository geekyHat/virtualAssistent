"""Route dei run durevoli (P-05/P-06, NewRay.md §19.3).

Creazione idempotente, lettura dello snapshot, cancellazione persistita e
stream degli eventi con replay/cursore. Fuori scope → 404 uniforme (§7.3),
come le altre risorse private.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import StreamingResponse

from newray.interfaces.http.dto.runs import CreateRunRequest, RunDTO
from newray.interfaces.http.errors import correlation_id
from newray.interfaces.http.middleware.identity import require_principal
from newray.interfaces.http.routes import API_PREFIX
from newray.kernel.errors import DomainError
from newray.kernel.identity import Principal
from newray.modules.identity import IdentityService
from newray.modules.runs import (
    EVENT_RUN_CANCELLED,
    EVENT_RUN_COMPLETED,
    EVENT_RUN_FAILED,
    EVENT_RUN_INTERRUPTED,
    DurableRun,
    DurableRunService,
    RunState,
)

logger = logging.getLogger(__name__)

#: Cadenza di poll iniziale dichiarata (NewRay.md §19.3 non fissa numeri),
#: da tarare in P-19/P-20. Nessun LISTEN/NOTIFY: stessa scelta di P-05.
DEFAULT_EVENTS_POLL_SECONDS = 0.5

_TERMINAL_STATES = frozenset(
    (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED, RunState.INTERRUPTED)
)
_TERMINAL_EVENT_TYPES = frozenset(
    (EVENT_RUN_COMPLETED, EVENT_RUN_FAILED, EVENT_RUN_CANCELLED, EVENT_RUN_INTERRUPTED)
)


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


def _sse_event(event: str, data: dict[str, object], *, event_id: int | None = None) -> str:
    id_line = f"id: {event_id}\n" if event_id is not None else ""
    return f"{id_line}event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _resync_payload(run: DurableRun) -> dict[str, object]:
    return {
        "state": str(run.state),
        "partial_text": run.partial_text,
        "finish_reason": run.finish_reason,
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "eval_duration_ns": run.eval_duration_ns,
    }


def build_router(poll_interval_seconds: float = DEFAULT_EVENTS_POLL_SECONDS) -> APIRouter:
    """Costruisce le route dei run; i servizi vengono da ``app.state``."""
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

    @router.get("/conversations/{conversation_id}/active-run", response_model=RunDTO | None)
    async def active_run(
        conversation_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> RunDTO | None:
        """Run non terminale più recente della conversazione, o ``null``
        (P-06: resume). Una scheda nuova o un refresh a metà generazione
        la chiamano per ritrovare e riprendere lo stream di un run in
        corso senza già possederne l'id; conversazione fuori scope →
        stesso ``null`` di "nessun run attivo", non un 404 distinto (RLS
        non fa emergere righe che il principal non può vedere)."""
        service: DurableRunService = request.app.state.durable_run_service
        run = await service.active_for_conversation(principal, conversation_id)
        return None if run is None else _run(run)

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

    @router.post("/runs/{run_id}/cancel", response_model=RunDTO)
    async def cancel_run(
        run_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> RunDTO:
        """Cancellazione persistita e idempotente (P-06, NewRay.md §8.3).

        Un run ``queued`` transita subito; un run ``running`` la vede
        propagata dal worker al prossimo heartbeat. Un run già terminale
        non cambia stato: la risposta riflette lo stato reale, non un
        successo fittizio."""
        service: DurableRunService = request.app.state.durable_run_service
        run = await service.cancel(principal, run_id)
        return _run(run)

    @router.get("/runs/{run_id}/events")
    async def stream_events(
        run_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(require_principal),
        after_sequence: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        """Eventi durevoli con replay/cursore (P-06, NewRay.md §19.3).

        Verifica scope prima di aprire lo stream (404 uniforme se il run
        non esiste/non è tuo, coerente con le altre risorse private); poi
        replay/poll con re-check della sessione a ogni giro — "la
        connessione non prolunga i grant". Cursore più vecchio del delta
        più vecchio rimasto (retention, 0011) → un solo evento ``resync``
        con lo snapshot corrente, mai un buco silenzioso.
        """
        service: DurableRunService = request.app.state.durable_run_service
        identity_service: IdentityService = request.app.state.identity_service
        session_id = principal.session_id
        cid = correlation_id(request)

        await service.get(principal, run_id)

        async def event_stream() -> AsyncIterator[str]:
            cursor = after_sequence
            try:
                while True:
                    page = await service.events(principal, run_id, cursor)
                    if page.gap:
                        snapshot = await service.get(principal, run_id)
                        yield _sse_event(
                            "resync", _resync_payload(snapshot), event_id=page.latest_sequence
                        )
                        cursor = page.latest_sequence
                        if snapshot.state in _TERMINAL_STATES:
                            return
                    else:
                        terminal = False
                        for evt in page.events:
                            yield _sse_event(evt.type, dict(evt.payload), event_id=evt.sequence)
                            cursor = evt.sequence
                            if evt.type in _TERMINAL_EVENT_TYPES:
                                terminal = True
                        if terminal:
                            return
                        if not page.events:
                            snapshot = await service.get(principal, run_id)
                            if snapshot.state in _TERMINAL_STATES:
                                return

                    try:
                        await asyncio.to_thread(identity_service.resolve_principal, session_id)
                    except DomainError:
                        yield _sse_event(
                            "error",
                            {"code": "SESSION_INVALID", "message": "sessione non più valida"},
                        )
                        return
                    await asyncio.sleep(poll_interval_seconds)
            except DomainError as exc:
                yield _sse_event("error", {"code": exc.code, "message": exc.message})
            except Exception:
                logger.exception("run %s: errore nello stream eventi (%s)", run_id, cid)
                yield _sse_event(
                    "error", {"code": "INTERNAL", "message": "errore interno del server"}
                )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-NewRay-Run-Mode": "durable",
            },
        )

    return router
