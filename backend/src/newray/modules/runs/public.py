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
from newray.modules.runs.durable import DurableRun, RunState, RunStore
from newray.modules.runs.durable_application import DurableRunService

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
]
