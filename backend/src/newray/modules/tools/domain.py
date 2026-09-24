"""Dominio del gateway tool (P-07, NewRay.md §§8.1, 10.3).

Il modulo ``tools`` possiede l'autorità sui tool: allowlist, schema dei
parametri e validazione. L'esito (``ToolResult``) e la porta consumata dal
worker vivono in ``runs`` (inversione di dipendenza, nessun ciclo). Qui non
compaiono FastAPI/SQL/SDK vendor: solo tipi Python.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

#: Codici stabili dell'esito tool (non errori HTTP: non attraversano il
#: trasporto verso il client, diventano ricevute e messaggi TOOL reimmessi
#: nel contesto). Il testo del modello non è evidenza di successo (§8.3).
TOOL_NOT_ALLOWED = "tool_not_allowed"
TOOL_SCHEMA_INVALID = "tool_schema_invalid"
TOOL_EXECUTION_FAILED = "tool_execution_failed"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Descrizione statica di un tool installato (NewRay.md §10.3).

    ``name`` è l'ID stabile mappato (es. ``run.status``), non il nome vendor.
    ``required_capability`` è la capacità che il principal deve avere in
    grant per vederlo nel toolset; ``None`` significa nessun grant richiesto
    (tool locale di sola lettura, senza effetti esterni). ``read_only``
    indica assenza di effetti esterni: un tool ``read_only`` non richiede
    approvazione (l'approval flow per i tool con effetti resta P-11/P-15).
    ``parameters`` è lo schema JSON dei soli argomenti ammessi.
    """

    name: str
    description: str
    parameters: Mapping[str, object] = field(default_factory=dict)
    required_capability: str | None = None
    read_only: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ToolSpec: il nome non può essere vuoto")


_TYPE_CHECKS: dict[str, object] = {
    "string": str,
    "boolean": bool,
    "object": Mapping,
    "array": (list, tuple),
}


def _type_ok(expected: str, value: object) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list | tuple)
    # Tipo non gestito dal validatore minimo: non bloccare (lo schema resta
    # la fonte, non inventiamo un rifiuto che il contratto non prevede).
    return True


def validate_arguments(schema: Mapping[str, object], arguments: Mapping[str, object]) -> str | None:
    """Valida gli argomenti (dato non fidato del modello) contro lo schema.

    Sottoinsieme minimo ma reale di JSON Schema per schemi ``object``:
    proprietà obbligatorie presenti, nessun argomento non previsto
    (allowlist dei parametri), tipi coerenti. Ritorna ``None`` se valido o
    un messaggio d'errore stabile altrimenti. Non è un validatore JSON
    Schema completo: gli schemi dei tool del pilot restano semplici.
    """
    if schema.get("type") != "object":
        return None
    properties = schema.get("properties")
    props: Mapping[str, object] = properties if isinstance(properties, Mapping) else {}
    required = schema.get("required")
    required_keys = required if isinstance(required, list | tuple) else ()
    for key in required_keys:
        if key not in arguments:
            return f"argomento obbligatorio mancante: {key}"
    for key, value in arguments.items():
        spec = props.get(key)
        if spec is None:
            return f"argomento non previsto: {key}"
        expected = spec.get("type") if isinstance(spec, Mapping) else None
        if isinstance(expected, str) and not _type_ok(expected, value):
            return f"tipo errato per {key!s}: atteso {expected}"
    return None
