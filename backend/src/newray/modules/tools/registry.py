"""Registry con allowlist e schema (§10, P-07).

Il registry è **l'unica** fonte di verità dei tool disponibili: il
gateway rifiuta ogni ``ToolCall`` per un nome che non è nell'allowlist.
Aggiungere un tool significa modificare il codice del bootstrap (§10):
non esiste installazione dinamica di tool da testo del modello, prompt
o annotazioni MCP.

Lo schema di ciascun tool è un sottoinsieme di JSON Schema, sufficiente
per il pilot: solo ``type: object`` a livello top con ``properties`` e
``required``. Ogni proprietà dichiara ``type`` (``string``/``integer``/
``boolean``/``number``) e opzionalmente ``enum``. Lo scopo è fermare
argomenti mal formati **prima** dell'esecuzione del tool: la validazione
avanzata (formati, min/max, refs) entra quando serve.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from newray.kernel.errors import DomainError
from newray.kernel.identity import Principal

from .domain import ToolCall, ToolResult


class ToolUnknownError(DomainError):
    """Il nome del tool non è nell'allowlist del registry (§10)."""

    code = "TOOL_UNKNOWN"


class ToolSchemaError(DomainError):
    """Gli argomenti della chiamata non corrispondono allo schema."""

    code = "TOOL_SCHEMA"


@dataclass(frozen=True, slots=True)
class ToolSchema:
    """Sottoinsieme JSON Schema usato per validare gli argomenti (§10).

    ``properties`` mappa nome → {"type": str, "enum"?: [values]}. Le
    proprietà elencate in ``required`` devono essere presenti negli
    argomenti; le altre sono opzionali e ignorate se assenti. Chiavi
    non dichiarate negli argomenti sono un errore (``additional_properties``
    a False, comportamento più stringente del default JSON Schema).
    """

    properties: Mapping[str, Mapping[str, object]]
    required: tuple[str, ...] = ()

    def validate(self, arguments: Mapping[str, object]) -> None:
        # additional_properties: strict False
        for key in arguments:
            if key not in self.properties:
                raise ToolSchemaError(f"argomento non dichiarato: {key!r}")
        for name in self.required:
            if name not in arguments:
                raise ToolSchemaError(f"argomento richiesto mancante: {name!r}")
        for name, value in arguments.items():
            spec = self.properties[name]
            _validate_property(name, value, spec)


def _validate_property(name: str, value: object, spec: Mapping[str, object]) -> None:
    kind = spec.get("type")
    if kind == "string":
        if not isinstance(value, str):
            raise ToolSchemaError(f"{name}: attesa stringa, ricevuto {type(value).__name__}")
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolSchemaError(f"{name}: atteso intero, ricevuto {type(value).__name__}")
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ToolSchemaError(f"{name}: atteso numero, ricevuto {type(value).__name__}")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise ToolSchemaError(f"{name}: atteso booleano, ricevuto {type(value).__name__}")
    else:
        raise ToolSchemaError(f"{name}: tipo dichiarato non supportato: {kind!r}")
    allowed = spec.get("enum")
    if allowed is not None:
        if not isinstance(allowed, list | tuple) or value not in allowed:
            raise ToolSchemaError(f"{name}: valore fuori enum")


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """Descrizione statica di un tool nel registry.

    ``requires_approval`` marca i tool che il gateway sospende
    (``waiting_approval``) prima di eseguire. In questa slice tutti i
    tool sono ``requires_approval=False``; la sospensione entrerà con la
    slice di approval flow.
    """

    name: str
    description: str
    schema: ToolSchema
    requires_approval: bool = False


class Tool(Protocol):
    """Contratto minimo di un tool NewRay.

    ``descriptor`` è statico e stabile per la vita del processo (entra
    nel prompt/tools del modello). ``execute`` riceve la ``ToolCall`` già
    validata e il principal risolto dal server: **non** deve
    interpretare gli argomenti come path/URL/identità.
    """

    @property
    def descriptor(self) -> ToolDescriptor: ...

    async def execute(self, principal: Principal, call: ToolCall) -> ToolResult: ...


@dataclass(frozen=True)
class ToolRegistry:
    """Allowlist di tool disponibili all'app.

    Il registry è iniettato dal bootstrap e **non** cambia a runtime:
    aggiungere un tool richiede una nuova build. Nessun tool installato
    da testo o annotazione MCP (§10).
    """

    tools: Mapping[str, Tool] = field(default_factory=dict)

    def descriptors(self) -> list[ToolDescriptor]:
        return [tool.descriptor for tool in self.tools.values()]

    def get(self, name: str) -> Tool:
        tool = self.tools.get(name)
        if tool is None:
            raise ToolUnknownError(f"tool non registrato: {name!r}")
        return tool

    def validate_call(self, call: ToolCall) -> None:
        """Prima barriera: nome noto e schema rispettato.

        Il gateway chiama questo prima di autorizzare l'invocazione.
        Un ``ToolCall`` che passa qui è **ben formato**; non è ancora
        autorizzato (grant, principal, capacità del profilo).
        """
        tool = self.get(call.tool_name)
        tool.descriptor.schema.validate(call.arguments)


__all__ = [
    "Tool",
    "ToolDescriptor",
    "ToolRegistry",
    "ToolSchema",
    "ToolSchemaError",
    "ToolUnknownError",
]
