"""Gateway tool: autorità server-side ed esecuzione (P-07, NewRay.md §§8.1, 10.3).

``RegistryToolGateway`` implementa la porta ``runs.ToolGateway``: tiene la
allowlist (registry per ID stabile), decide il toolset come intersezione fra
tool installati e grant del principal, valida gli argomenti (dato non fidato
del modello) contro lo schema, ed esegue sotto lo scope del principal. Il
gateway non persiste nulla: la ricevuta durevole e gli eventi sono scritti
dal worker (fencing-checked) — qui vive solo l'autorità e l'esecuzione.
"""

from __future__ import annotations

import logging

from newray.kernel.identity import Principal
from newray.modules.models import ToolCallRequest, ToolSchema
from newray.modules.runs import ToolResult

from .domain import (
    TOOL_EXECUTION_FAILED,
    TOOL_NOT_ALLOWED,
    TOOL_SCHEMA_INVALID,
    validate_arguments,
)
from .ports import Tool

logger = logging.getLogger(__name__)


class RegistryToolGateway:
    """Registry + autorità + esecuzione dei tool installati."""

    def __init__(self, tools: tuple[Tool, ...]) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            name = tool.spec.name
            if name in self._tools:
                raise ValueError(f"tool duplicato nel registry: {name}")
            self._tools[name] = tool

    def _granted(self, principal: Principal, capability: str | None) -> bool:
        # Un tool senza capacità richiesta (locale, sola lettura) è sempre
        # ammesso al proprietario del run. Il layer dei grant per tool con
        # capacità/effetti non esiste ancora (gate P-11/P-15 con l'approval):
        # finché non c'è, un tool con capacità richiesta resta fuori dal
        # toolset (default sicuro), mai concesso implicitamente.
        del principal
        return capability is None

    def _allowed(self, principal: Principal) -> dict[str, Tool]:
        return {
            name: tool
            for name, tool in self._tools.items()
            if self._granted(principal, tool.spec.required_capability)
        }

    def tool_schemas(self, principal: Principal) -> tuple[ToolSchema, ...]:
        return tuple(
            ToolSchema(
                name=tool.spec.name,
                description=tool.spec.description,
                parameters=tool.spec.parameters,
            )
            for tool in self._allowed(principal).values()
        )

    def invoke(self, principal: Principal, call: ToolCallRequest) -> ToolResult:
        tool = self._allowed(principal).get(call.name)
        if tool is None:
            # Tool sconosciuto o non nel toolset del principal: fermarsi
            # (NewRay.md §8.1), non eseguire nulla. Codice stabile.
            return ToolResult(
                content=f'{{"error": "{TOOL_NOT_ALLOWED}", "tool": "{call.name}"}}',
                is_error=True,
                error_code=TOOL_NOT_ALLOWED,
            )
        schema_error = validate_arguments(tool.spec.parameters, call.arguments)
        if schema_error is not None:
            return ToolResult(
                content=f'{{"error": "{TOOL_SCHEMA_INVALID}"}}',
                is_error=True,
                error_code=TOOL_SCHEMA_INVALID,
            )
        try:
            return tool.execute(principal.scope, call.arguments)
        except Exception:
            # Guasto imprevisto dell'esecuzione: nessun retry cieco, esito
            # esplicito (§8.3). I dettagli restano nei log, non nel risultato.
            logger.exception("tool %s: errore di esecuzione", call.name)
            return ToolResult(
                content=f'{{"error": "{TOOL_EXECUTION_FAILED}"}}',
                is_error=True,
                error_code=TOOL_EXECUTION_FAILED,
            )
