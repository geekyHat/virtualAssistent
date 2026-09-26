"""Dominio del ciclo tool (NewRay.md §10; P-07).

Definisce i tipi immutabili del ciclo: ``ToolCall`` è ciò che il modello
propone, ``ToolResult`` è ciò che il gateway rimanda al modello, senza
mai attribuire successo al solo testo del modello (§8.3). Gli stati di
un'invocazione (``prepared`` → ``executing`` → ``succeeded``/``failed``/
``outcome_unknown``) sono quelli del ticket P-07; ``awaiting_approval``
entrerà con la slice del flusso approvazione.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType


class ToolInvocationState(StrEnum):
    """Stati distinti di un'invocazione (§8.3, P-07).

    ``outcome_unknown`` è la trappola per gli esiti incerti: mai
    trasformato in ``succeeded`` da un retry cieco, mai da testo del
    modello.
    """

    PREPARED = "prepared"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


TERMINAL_INVOCATION_STATES = frozenset(
    {
        ToolInvocationState.SUCCEEDED,
        ToolInvocationState.FAILED,
        ToolInvocationState.OUTCOME_UNKNOWN,
    }
)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Proposta di chiamata tool emessa dal modello.

    ``call_id`` è l'identificatore stabile con cui il gateway correla
    la chiamata al risultato (``ToolResult.call_id``). ``tool_name`` è
    il nome registrato nell'allowlist; ``arguments`` è un dizionario
    JSON-serializzabile (dict di solo str→primitivi/dict/list).

    Gli argomenti sono **dati non fidati** (§10): il modello può
    proporre qualsiasi valore. La validazione contro lo schema del tool
    e le regole di autorizzazione stanno nel gateway, non qui.
    """

    call_id: str
    tool_name: str
    arguments: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.call_id:
            raise ValueError("ToolCall: call_id non può essere vuoto")
        if not self.tool_name:
            raise ValueError("ToolCall: tool_name non può essere vuoto")
        object.__setattr__(self, "arguments", _freeze(self.arguments))


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Esito di una chiamata tool.

    ``call_id`` correla il risultato alla ``ToolCall``. ``state`` è lo
    stato terminale (o ``outcome_unknown``); ``payload`` è ciò che il
    modello riceverà nel prossimo turno come ``role="tool"``. Il testo
    del payload non è mai promosso a "successo": deve arrivare qui
    solo se ``state == succeeded``.
    """

    call_id: str
    tool_name: str
    state: ToolInvocationState
    payload: Mapping[str, object]
    problems: tuple[str, ...] = ()
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.state not in TERMINAL_INVOCATION_STATES:
            raise ValueError(f"ToolResult: state deve essere terminale, ricevuto {self.state}")
        if self.state == ToolInvocationState.SUCCEEDED and self.problems:
            raise ValueError("ToolResult: 'succeeded' non ammette problems")
        object.__setattr__(self, "payload", _freeze(self.payload))


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Record di un'invocazione (per la outbox futura P-07 sub-slice).

    ``receipt`` è la chiave di idempotenza dell'invocazione all'interno
    del run: due tentativi con la stessa (run_id, call_id) hanno lo
    stesso esito (non un secondo effetto).
    """

    id: uuid.UUID
    run_id: uuid.UUID
    call: ToolCall
    state: ToolInvocationState
    result: ToolResult | None
    created_at: datetime
    updated_at: datetime


def _freeze(value: object) -> object:
    """Copia profonda in strutture immutabili per gli argomenti/payload.

    Accetta solo tipi JSON-serializzabili: valore/lista/dict con chiavi
    stringa. Solleva ``ValueError`` su strutture non conformi (evita che
    un client passi oggetti Python arbitrari come argomento).
    """
    if isinstance(value, Mapping):
        if not all(isinstance(k, str) for k in value):
            raise ValueError("chiavi non stringa in argomenti/payload del tool")
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, tuple | list):
        return tuple(_freeze(v) for v in value)
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise ValueError(f"tipo non JSON-serializzabile in tool: {type(value).__name__}")


__all__ = [
    "TERMINAL_INVOCATION_STATES",
    "ToolCall",
    "ToolInvocation",
    "ToolInvocationState",
    "ToolResult",
]
