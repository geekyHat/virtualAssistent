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
    TERMINAL_STATES,
    ClaimedRun,
    DurableRun,
    RunState,
    RunStore,
)
from newray.modules.runs.durable_application import DurableRunService
from newray.modules.runs.worker import DurableRunWorker, WorkerConfig

__all__ = [
    "INLINE_CONTEXT_MAX_CHARACTERS",
    "INLINE_CONTEXT_MAX_MESSAGES",
    "INLINE_MAX_OUTPUT_TOKENS",
    "InlineCompleted",
    "InlineDelta",
    "InlineRunEvent",
    "InlineRunService",
    "PreparedInlineRun",
    "ClaimedRun",
    "DurableRun",
    "RunState",
    "RunStore",
    "TERMINAL_STATES",
    "DurableRunService",
    "DurableRunWorker",
    "WorkerConfig",
]
