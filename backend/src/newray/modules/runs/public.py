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
    TERMINAL_EVENT_TYPES,
    ClaimedRun,
    DurableRun,
    RunEvent,
    RunEventReader,
    RunEventType,
    RunState,
    RunStore,
)
from newray.modules.runs.durable_application import DurableRunService
from newray.modules.runs.executor import ChatModelRunExecutor
from newray.modules.runs.launcher import RunLauncher
from newray.modules.runs.worker import (
    CancelRequested,
    LeaseLost,
    PartialCheckpoint,
    RunExecutor,
    RunOutcome,
    Worker,
)

__all__ = [
    "INLINE_CONTEXT_MAX_CHARACTERS",
    "INLINE_CONTEXT_MAX_MESSAGES",
    "INLINE_MAX_OUTPUT_TOKENS",
    "InlineCompleted",
    "InlineDelta",
    "InlineRunEvent",
    "InlineRunService",
    "PreparedInlineRun",
    "CancelRequested",
    "ChatModelRunExecutor",
    "ClaimedRun",
    "DurableRun",
    "RunEvent",
    "RunEventReader",
    "RunEventType",
    "RunState",
    "RunStore",
    "DurableRunService",
    "LeaseLost",
    "PartialCheckpoint",
    "RunExecutor",
    "RunLauncher",
    "RunOutcome",
    "TERMINAL_EVENT_TYPES",
    "Worker",
]
