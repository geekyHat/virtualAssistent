"""DTO pubblici dell'identità (NewRay.md §19.1, §19.2)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from newray.kernel.identity import Role


class BootstrapRequest(BaseModel):
    """Richiesta di bootstrap monouso: nome del proprietario + credenziale.

    La credenziale non viene mai persistita in chiaro: il caso d'uso ne
    calcola il record hash con ``newray.kernel.crypto`` (B-03.2-14). La
    lunghezza minima applicativa è verificata dal kernel; qui il limite
    superiore protegge da payload gonfiati senza fare da oracolo
    sull'algoritmo di hashing.
    """

    display_name: str = Field(min_length=1, max_length=100)
    credential: str = Field(min_length=1, max_length=4096)


class LoginRequest(BaseModel):
    """Richiesta di login del proprietario esistente (B-03.2-14).

    Owner sconosciuto e credenziale sbagliata collassano in un unico
    ``INVALID_CREDENTIALS``: il canale non è un oracolo per scoprire il
    nome scelto in bootstrap.
    """

    display_name: str = Field(min_length=1, max_length=100)
    credential: str = Field(min_length=1, max_length=4096)


class SessionStatusDTO(BaseModel):
    """Stato pubblico dell'installazione (``GET /session/status``).

    Serve alla WebUI per decidere se mostrare bootstrap o login; non
    rivela il nome dell'owner né alcun dettaglio derivato dalla
    sessione, solo la presenza dell'owner.
    """

    bootstrapped: bool


class IdentityDTO(BaseModel):
    """Identità pubblica del principal risolto dal server.

    Niente campi sensibili oltre il minimo necessario alla WebUI:
    nessun token, nessun dettaglio della sessione.
    """

    user_id: uuid.UUID
    display_name: str
    role: Role
