"""Principal, scope e identificatori: identità risolta solo dal server.

Tre assi distinti (NewRay.md §7.1): il ruolo account (``Role``, qui), il
profilo AI (``AProfile``, posseduto dal modulo ``profiles``) e il permesso
(azione su risorsa, deciso dal modulo ``access``). Scegliere un profilo non
concede ruoli né permessi: ``Principal`` non contiene alcun campo profilo.

Gli identificatori inviati dal browser sono riferimenti da verificare, mai
identità fidate: ``Principal`` nasce dalla sessione risolta dal server.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum


def new_id() -> uuid.UUID:
    """Nuovo identificatore opaco (UUID v4): mai derivato dal client."""
    return uuid.uuid4()


class Role(StrEnum):
    """Ruolo account del principal (NewRay.md §7.1, 7.2).

    Asse separato dal profilo AI: non descrive capacità del modello né
    strumenti disponibili.
    """

    OWNER = "owner"
    MEMBER = "member"
    ADMINISTRATOR = "administrator"


@dataclass(frozen=True, slots=True)
class Scope:
    """Ambito obbligatorio di ogni operazione su dati privati (NewRay.md §7.3).

    Lo scope è completo o l'operazione non parte: un filtro facoltabile
    (``user_id=None``) non è una convenzione accettabile.
    """

    organization_id: uuid.UUID
    user_id: uuid.UUID

    def __post_init__(self) -> None:
        if not isinstance(self.organization_id, uuid.UUID) or not isinstance(
            self.user_id, uuid.UUID
        ):
            raise TypeError("Scope incompleto: organization_id e user_id sono obbligatori")


@dataclass(frozen=True, slots=True)
class Principal:
    """Identità effettiva di una richiesta o job, risolta dal server.

    Portato da ogni richiesta/job (NewRay.md §7.1); non contiene il profilo
    AI, che è selezionato per run e conservato nello snapshot del run.
    """

    user_id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID
    role: Role

    @property
    def scope(self) -> Scope:
        """Ambito obbligatorio per le operazioni sui dati privati del principal."""
        return Scope(self.organization_id, self.user_id)
