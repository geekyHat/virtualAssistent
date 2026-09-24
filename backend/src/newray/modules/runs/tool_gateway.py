"""Porta del gateway tool vista dal worker dei run (P-07, NewRay.md §8.1, §10.3).

Il worker (in questo modulo) esegue il ciclo tool ma non conosce
l'implementazione del gateway: dipende solo da questa porta e dai tipi di
risultato qui definiti. Il gateway concreto vive nel modulo ``tools`` e
importa ``runs`` (per lo store di sola lettura del tool ``run.status``); il
bootstrap collega l'implementazione. L'inversione di dipendenza tiene
``runs`` senza import di ``tools``: nessun ciclo di modulo.

L'autorità è server-side (NewRay.md §7): allowlist, validazione schema e
grant sono responsabilità del gateway, mai del testo del modello. Gli
argomenti della chiamata sono dati non fidati e non scelgono principal o
path (§8.1, §10.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from newray.kernel.identity import Principal
from newray.modules.models import ToolCallRequest, ToolSchema


class ToolInvocationState(StrEnum):
    """Stati di una invocazione tool (NewRay.md §8.3).

    Il vocabolario completo è stabile e fissato dallo schema; questo pilot
    esercita solo ``EXECUTING``/``SUCCEEDED``/``FAILED`` (tool locale di sola
    lettura, sincrono, senza effetti esterni). ``PREPARED`` collassa in
    ``EXECUTING`` senza attesa; ``AWAITING_APPROVAL`` e ``OUTCOME_UNKNOWN``
    restano non usati finché non esiste un tool con effetti esterni
    (approval e riconciliazione: gate esplicito P-11/P-15).
    """

    PREPARED = "prepared"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Esito di una invocazione dal gateway (NewRay.md §8.1).

    ``content`` è il testo del risultato reimmesso nel contesto come
    messaggio TOOL della continuazione. ``is_error`` distingue un fallimento
    (schema/allowlist/esecuzione) da un successo: il testo del modello non è
    evidenza di successo (§8.3). ``error_code`` è un codice stabile quando
    ``is_error``.
    """

    content: str
    is_error: bool = False
    error_code: str | None = None


class ToolGateway(Protocol):
    """Autorità e esecuzione dei tool per un run (NewRay.md §§8.1, 10.3)."""

    def tool_schemas(self, principal: Principal) -> tuple[ToolSchema, ...]:
        """Toolset offerto al modello: intersezione fra capacità del profilo,
        tool installati e grant del principal. Vuoto se nessun tool è
        ammesso per questo principal."""
        ...

    def invoke(self, principal: Principal, call: ToolCallRequest) -> ToolResult:
        """Valida (allowlist + schema + grant) ed esegue la chiamata sotto lo
        scope del principal, mai sotto un principal/path derivato dagli
        argomenti. Ritorna un ``ToolResult``; un guasto è ``is_error``, non
        un'eccezione che interrompe il ciclo del worker."""
        ...
