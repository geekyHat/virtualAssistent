"""Primo tool concreto: stato del proprio run (P-07).

Non ha effetti esterni: legge lo snapshot del run tramite
``DurableRunService`` **usando il principal risolto dal server**. Gli
argomenti del modello non scelgono principal né bypassano lo scope:
un ``run_id`` fuori scope ritorna ``failed`` con motivo, non un accesso
indebito.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from newray.kernel.errors import DomainError, NotFound
from newray.kernel.identity import Principal
from newray.modules.runs import DurableRunService

from ..domain import ToolCall, ToolInvocationState, ToolResult
from ..registry import Tool, ToolDescriptor, ToolSchema


class RunStatusTool:
    """`run_status(run_id)` → snapshot autorevole dello stato del run.

    Utile per il modello per **osservare** il proprio stato quando la
    conversazione continua dopo una cancellazione, un errore o un
    troncamento. Nessun effetto: solo lettura scoped.
    """

    descriptor = ToolDescriptor(
        name="run_status",
        description=(
            "Restituisce lo stato autorevole di un run del proprio scope. "
            "Argomento: run_id (UUID). Nessun effetto collaterale."
        ),
        schema=ToolSchema(
            properties={"run_id": {"type": "string"}},
            required=("run_id",),
        ),
        requires_approval=False,
    )

    def __init__(self, runs: DurableRunService) -> None:
        self._runs = runs

    async def execute(self, principal: Principal, call: ToolCall) -> ToolResult:
        try:
            run_id = uuid.UUID(str(call.arguments["run_id"]))
        except (ValueError, TypeError):
            return ToolResult(
                call_id=call.call_id,
                tool_name=self.descriptor.name,
                state=ToolInvocationState.FAILED,
                payload={},
                problems=(f"run_id non è un UUID: {call.arguments.get('run_id')!r}",),
                completed_at=datetime.now(UTC),
            )
        try:
            run = await self._runs.get(principal, run_id)
        except NotFound:
            return ToolResult(
                call_id=call.call_id,
                tool_name=self.descriptor.name,
                state=ToolInvocationState.FAILED,
                payload={},
                problems=("run non trovato nello scope corrente",),
                completed_at=datetime.now(UTC),
            )
        except DomainError as exc:
            return ToolResult(
                call_id=call.call_id,
                tool_name=self.descriptor.name,
                state=ToolInvocationState.FAILED,
                payload={},
                problems=(f"{exc.code}: {exc.message}",),
                completed_at=datetime.now(UTC),
            )
        return ToolResult(
            call_id=call.call_id,
            tool_name=self.descriptor.name,
            state=ToolInvocationState.SUCCEEDED,
            payload={
                "run_id": str(run.id),
                "state": run.state.value,
                "finish_reason": run.finish_reason,
                "prompt_tokens": run.prompt_tokens,
                "completion_tokens": run.completion_tokens,
                "eval_duration_ns": run.eval_duration_ns,
                "cancel_requested": run.cancel_requested_at is not None,
            },
            completed_at=datetime.now(UTC),
        )


# Ai fini della verifica del contratto: RunStatusTool implementa Tool.
_: Tool = RunStatusTool.__new__(RunStatusTool)


__all__ = ["RunStatusTool"]
