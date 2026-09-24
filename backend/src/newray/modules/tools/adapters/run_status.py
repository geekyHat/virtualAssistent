"""Tool ``run.status``: stato del proprio run, locale e di sola lettura (P-07).

Primo caso concreto del gateway (NewRay.md §8.1): nessun effetto esterno,
nessuna approvazione. Dimostra la proprietà di sicurezza dei criteri di
accettazione: il modello fornisce ``run_id`` come argomento, ma lo scope
dell'accesso è quello del principal, iniettato dal gateway — mai derivato
dall'argomento. Un ``run_id`` di un altro proprietario risolve a "non
trovato" sotto RLS, non a una lettura cross-tenant.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping

from newray.kernel.identity import Scope
from newray.modules.runs import RunStore, ToolResult

from ..domain import TOOL_EXECUTION_FAILED, ToolSpec

_SPEC = ToolSpec(
    name="run.status",
    description=(
        "Riporta lo stato di un run dell'utente corrente (queued/running/"
        "completed/failed/cancelled/interrupted) dato il suo identificatore."
    ),
    parameters={
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "Identificatore (UUID) del run da ispezionare.",
            }
        },
        "required": ["run_id"],
    },
    required_capability=None,
    read_only=True,
)


class RunStatusTool:
    """Legge lo snapshot pubblico di un run sotto lo scope del principal."""

    def __init__(self, store: RunStore) -> None:
        self._store = store

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    def execute(self, scope: Scope, arguments: Mapping[str, object]) -> ToolResult:
        raw = arguments.get("run_id")
        try:
            run_id = uuid.UUID(str(raw))
        except (ValueError, TypeError):
            return ToolResult(
                content=json.dumps({"error": "invalid_run_id"}),
                is_error=True,
                error_code=TOOL_EXECUTION_FAILED,
            )
        # Scope del principal, non dell'argomento: un run altrui → None.
        run = self._store.get(scope, run_id)
        if run is None:
            return ToolResult(content=json.dumps({"run_id": str(run_id), "found": False}))
        return ToolResult(
            content=json.dumps(
                {
                    "run_id": str(run.id),
                    "found": True,
                    "state": str(run.state),
                    "finish_reason": run.finish_reason,
                    "prompt_tokens": run.prompt_tokens,
                    "completion_tokens": run.completion_tokens,
                }
            )
        )
