"""API pubblica del modulo tools (NewRay.md §5.1; P-07).

Unico punto di import per bootstrap e altri moduli: mai ``adapters`` o
dettagli privati. Il gateway concreto (``RegistryToolGateway``) implementa
la porta ``runs.ToolGateway``; il bootstrap lo costruisce con i tool
installati e lo collega al worker.
"""

from newray.modules.tools.application import RegistryToolGateway
from newray.modules.tools.domain import (
    TOOL_EXECUTION_FAILED,
    TOOL_NOT_ALLOWED,
    TOOL_SCHEMA_INVALID,
    ToolSpec,
    validate_arguments,
)
from newray.modules.tools.ports import Tool

__all__ = [
    "RegistryToolGateway",
    "Tool",
    "ToolSpec",
    "TOOL_NOT_ALLOWED",
    "TOOL_SCHEMA_INVALID",
    "TOOL_EXECUTION_FAILED",
    "validate_arguments",
]
