"""API pubblica del modulo runs (NewRay.md §5.1)."""

from newray.modules.runs.application import (
    INLINE_CONTEXT_MAX_CHARACTERS,
    INLINE_CONTEXT_MAX_MESSAGES,
    INLINE_MAX_OUTPUT_TOKENS,
    InlineRunService,
)
from newray.modules.runs.domain import (
    InlineCompleted,
    InlineDelta,
    InlineRunEvent,
    PreparedInlineRun,
)
from newray.modules.runs.durable import (
    FINISH_REASON_DEADLINE_EXCEEDED,
    FINISH_REASON_EMPTY_OUTPUT,
    FINISH_REASON_MODEL_ERROR,
    FINISH_REASON_WORKER_LOST,
    DurableRun,
    RunState,
    RunStore,
)
from newray.modules.runs.durable_application import DurableRunService
from newray.modules.runs.worker import RunWorker

__all__ = [
    "INLINE_CONTEXT_MAX_CHARACTERS",
    "INLINE_CONTEXT_MAX_MESSAGES",
    "INLINE_MAX_OUTPUT_TOKENS",
    "InlineCompleted",
    "InlineDelta",
    "InlineRunEvent",
    "InlineRunService",
    "PreparedInlineRun",
    "DurableRun",
    "RunState",
    "RunStore",
    "DurableRunService",
    "RunWorker",
    "FINISH_REASON_DEADLINE_EXCEEDED",
    "FINISH_REASON_EMPTY_OUTPUT",
    "FINISH_REASON_MODEL_ERROR",
    "FINISH_REASON_WORKER_LOST",
]
