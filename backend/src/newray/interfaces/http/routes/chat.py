"""Trasporto SSE della preview inline transitoria (B-03.2-36).

Il caso d'uso appartiene al modulo ``runs``. Questa route è deprecata e sarà
sostituita da ``POST /conversations/{id}/runs`` con worker durevole (B-04/05):
non è un secondo motore chat e non promette continuità alla disconnessione.

Cancellazione: il client chiude la connessione e il backend chiude lo stream
del modello. La preview non persiste né il prompt né il frammento incompleto;
la cancellazione durevole e l'output parziale appartengono a B-04–B-07.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from starlette.responses import StreamingResponse

from newray.interfaces.http.dto.chat import RunRequest
from newray.interfaces.http.errors import correlation_id
from newray.interfaces.http.middleware.identity import require_principal
from newray.interfaces.http.routes import API_PREFIX
from newray.kernel.errors import DomainError
from newray.kernel.identity import Principal
from newray.modules.runs import InlineCompleted, InlineDelta, InlineRunService


def _sse_event(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def build_router() -> APIRouter:
    router = APIRouter(prefix=API_PREFIX)

    @router.post(
        "/conversations/{conversation_id}/run",
        deprecated=True,
        summary="Preview inline transitoria; usare i run durevoli quando disponibili",
    )
    async def run_chat(
        conversation_id: uuid.UUID,
        request: Request,
        body: RunRequest,
        principal: Principal = Depends(require_principal),
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key", min_length=1, max_length=96),
        ] = None,
    ) -> StreamingResponse:
        service: InlineRunService = request.app.state.inline_run_service
        cid = correlation_id(request)
        prepared = await service.prepare(
            principal,
            conversation_id,
            body.profile_id,
            body.content,
        )

        async def event_stream() -> AsyncIterator[str]:
            try:
                async for event in service.execute(
                    principal,
                    prepared,
                    idempotency_key=idempotency_key,
                ):
                    if isinstance(event, InlineDelta):
                        yield _sse_event("delta", {"text": event.text})
                    elif isinstance(event, InlineCompleted):
                        completion = event.completion
                        yield _sse_event(
                            "done",
                            {
                                "finish_reason": (
                                    completion.finish_reason if completion else "replayed"
                                ),
                                "message_id": str(event.assistant_message.id),
                                "user_message_id": str(event.user_message.id),
                                "prompt_tokens": (completion.prompt_tokens if completion else None),
                                "completion_tokens": (
                                    completion.completion_tokens if completion else None
                                ),
                                "eval_duration_ns": (
                                    completion.eval_duration_ns if completion else None
                                ),
                                "tokens_per_second": (
                                    completion.tokens_per_second if completion else None
                                ),
                                "model": prepared.binding.model_name,
                                "digest": prepared.binding.digest,
                                "context_message_count": prepared.context_message_count,
                                "context_character_count": prepared.context_character_count,
                                "context_truncated": prepared.context_truncated,
                                "max_output_tokens": prepared.chat_request.max_tokens,
                                "replayed": event.replayed,
                                "correlation_id": cid,
                            },
                        )
            except DomainError as exc:
                yield _sse_event("error", {"code": exc.code, "message": exc.message})
                return
            except Exception:
                yield _sse_event(
                    "error",
                    {"code": "INTERNAL", "message": "errore interno del server"},
                )
                return

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-NewRay-Run-Mode": "inline-preview",
            },
        )

    return router
