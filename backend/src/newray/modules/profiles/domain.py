"""Profili AI, versioni e binding dei modelli (NewRay.md §§3.1, 7.2, 9.1).

Il profilo AI è un asse *distinto* dal ruolo account
(``newray.kernel.identity.Role``): scegliere Coder non autorizza una shell
né file aggiuntivi, e nessun run cambia modello dietro le quinte
(ADR 0003). Il profilo è selezionato manualmente dall'utente per run e
vive nello snapshot del run, non nel ``Principal``.

Le versioni e i binding sono entità di configurazione immutabili
(§7.2): le incorpora lo snapshot del run (B-04) e non subiscono
aggiornamenti nascosti. ``ResolvedBinding`` è il valore di quella
risoluzione: stesso stato → stesso snapshot, riproducibile.

I fatti sui modelli (``ModelInfo``, ``ModelStatus``, runtime, limiti del
nome modello) appartengono al modulo ``models`` (B-03): qui si riferiscono
solo, mai si riducono.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from newray.modules.models import MAX_MODEL_NAME_LENGTH, RUNTIME_OLLAMA, ModelInfo

#: Limiti di lunghezza: fonte unica per dominio, DTO e schema
#: (NewRay.md §19.2).
MAX_PROFILE_NAME_LENGTH = 100
MAX_PROFILE_VERSION_LENGTH = 40
MAX_BINDING_NAME_LENGTH = 100
MAX_INSTRUCTIONS_LENGTH = 20_000
MAX_IDEMPOTENCY_KEY_LENGTH = 128

#: Nome del binding del nuovo Assistente. Distinto da ``local-default``
#: delle installazioni precedenti: un seeding parziale storico non deve
#: associare silenziosamente il candidato pilot al vecchio modello.
DEFAULT_BINDING_NAME = "local-assistant"

#: Versione dei profili creati dal seeding di default.
DEFAULT_PROFILE_VERSION = "1.0.0"


class AProfile(StrEnum):
    """Profili AI selezionabili manualmente dall'utente."""

    ASSISTANT = "assistant"
    RESEARCHER = "researcher"
    CODER = "coder"


@dataclass(frozen=True, slots=True)
class ModelBinding:
    """Riferimento di un profilo a runtime e modello (NewRay.md §9.1).

    Identifica runtime, modello e parametri: la quantizzazione è parte
    della convenzione del nome Ollama, non un campo separato.
    """

    id: uuid.UUID
    organization_id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    runtime: str
    model_name: str
    parameters: dict[str, object]
    created_at: datetime

    def __post_init__(self) -> None:
        if not 1 <= len(self.name) <= MAX_BINDING_NAME_LENGTH:
            raise ValueError(f"ModelBinding: nome fuori dai limiti (1-{MAX_BINDING_NAME_LENGTH})")
        if self.runtime != RUNTIME_OLLAMA:
            raise ValueError(f"ModelBinding: runtime non supportato ({self.runtime!r})")
        if not 1 <= len(self.model_name) <= MAX_MODEL_NAME_LENGTH:
            raise ValueError(f"ModelBinding: modello fuori dai limiti (1-{MAX_MODEL_NAME_LENGTH})")


@dataclass(frozen=True, slots=True)
class Profile:
    """Profilo AI posseduto dall'utente: un tipo per proprietario (§7.2)."""

    id: uuid.UUID
    organization_id: uuid.UUID
    owner_id: uuid.UUID
    kind: AProfile
    display_name: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not 1 <= len(self.display_name) <= MAX_PROFILE_NAME_LENGTH:
            raise ValueError(
                f"Profile: nome visualizzato fuori dai limiti (1-{MAX_PROFILE_NAME_LENGTH})"
            )


@dataclass(frozen=True, slots=True)
class ProfileVersion:
    """Versione immutabile di un profilo: binding, istruzioni, data.

    È il riferimento stabile degli snapshot dei run (NewRay.md §7.2,
    §8.1 passo 3): nessuna modifica in-place, mai.
    """

    id: uuid.UUID
    profile_id: uuid.UUID
    organization_id: uuid.UUID
    owner_id: uuid.UUID
    version: str
    model_binding_id: uuid.UUID
    instructions: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not 1 <= len(self.version) <= MAX_PROFILE_VERSION_LENGTH:
            raise ValueError(
                f"ProfileVersion: versione fuori dai limiti (1-{MAX_PROFILE_VERSION_LENGTH})"
            )
        if len(self.instructions) > MAX_INSTRUCTIONS_LENGTH:
            raise ValueError("ProfileVersion: istruzioni oltre il limite")


@dataclass(frozen=True, slots=True)
class ResolvedBinding:
    """Snapshot riproducibile della risoluzione del binding
    (NewRay.md §8.1 passo 3, ADR 0003).

    Valore immutabile: lo stesso profilo sullo stesso stato del catalogo
    produce lo stesso snapshot. I run (B-04) lo incorporano senza
    riferimenti mutevoli; la modifica di un binding vale solo per i run
    successivi (§9.1).
    """

    profile_id: uuid.UUID
    profile_version_id: uuid.UUID
    binding_id: uuid.UUID
    profile_version: str
    binding_name: str
    runtime: str
    model_name: str
    digest: str
    parameters: Mapping[str, object]
    capabilities: tuple[str, ...]
    instructions: str = ""

    def __post_init__(self) -> None:
        if not self.digest:
            raise ValueError("ResolvedBinding: il digest del modello è obbligatorio")
        object.__setattr__(self, "parameters", _freeze_parameters(self.parameters))


def _freeze_parameters(value: Mapping[str, object]) -> Mapping[str, object]:
    """Copia e congela ricorsivamente parametri JSON-serializzabili.

    Uno snapshot non deve condividere dict/list con binding, adapter o caller:
    una mutazione successiva cambierebbe il significato di un run già creato.
    """
    return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})


def _freeze_json(value: object) -> object:
    """Restituisce una copia profonda e non mutabile di un valore JSON."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("ResolvedBinding: le chiavi dei parametri devono essere stringhe")
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(item) for item in value)
    raise ValueError("ResolvedBinding: parametri non JSON-serializzabili")


@dataclass(frozen=True, slots=True)
class ProfileView:
    """Profilo con versione corrente, binding e disponibilità del modello.

    ``model`` è ``None`` quando il modello del binding non è nel
    catalogo: l'interfaccia lo mostra come non disponibile, e la
    risoluzione risponde ``MODEL_UNAVAILABLE`` (§19.4).
    """

    profile: Profile
    version: ProfileVersion
    binding: ModelBinding
    model: ModelInfo | None
