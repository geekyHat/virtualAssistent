"""Porte del modulo tools (P-07, NewRay.md §§5.1, 10.3).

Un ``Tool`` è un'operazione installata: espone lo spec (allowlist e schema)
ed esegue sotto lo scope del principal fornito dal gateway, mai sotto un
principal derivato dagli argomenti del modello (§8.1, §10.3). L'esecuzione è
sincrona: i tool del pilot sono locali e senza effetti esterni; un futuro
tool con I/O di rete userà comunque questa porta e il gateway per l'autorità.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from newray.kernel.identity import Scope
from newray.modules.runs import ToolResult

from .domain import ToolSpec


class Tool(Protocol):
    """Operazione installata con spec e esecuzione scoped."""

    @property
    def spec(self) -> ToolSpec: ...

    def execute(self, scope: Scope, arguments: Mapping[str, object]) -> ToolResult:
        """Esegue sotto ``scope`` (del principal, non degli argomenti).
        Ritorna un ``ToolResult``; i guasti attesi sono ``is_error`` con
        codice stabile, non eccezioni (il gateway cattura comunque quelle
        impreviste)."""
        ...
