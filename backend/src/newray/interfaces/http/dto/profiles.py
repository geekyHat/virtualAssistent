"""DTO pubblici di profili, versioni e binding dei modelli (NewRay.md §19.2; B-02).

I limiti provengono dal dominio come fonte unica: il DTO riflette i
vincoli, il dominio li riafferma ai confini interni. Lo stato del modello
(installed/compatible/qualified) è esplicito: sono fatti diversi (§9.1).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from newray.modules.models import MAX_MODEL_NAME_LENGTH, ModelStatus, ReadinessState
from newray.modules.profiles import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_INSTRUCTIONS_LENGTH,
    MAX_PROFILE_VERSION_LENGTH,
    AProfile,
)


class ModelInfoDTO(BaseModel):
    """Modello del runtime con digest e stato (NewRay.md §9.1).

    Il digest identifica l'artefatto per la riproducibilità; i tag
    mobili restano scoperta, mai riferimento degli snapshot.
    """

    name: str
    runtime: str
    digest: str | None
    status: ModelStatus
    capabilities: list[str]


class BindingDTO(BaseModel):
    """Binding del profilo a runtime e modello, con parametri espliciti."""

    name: str
    runtime: str
    model_name: str
    parameters: dict[str, object]


class ProfileDTO(BaseModel):
    """Profilo con versione corrente, binding e disponibilità del modello.

    ``model`` è ``null`` quando il modello del binding non è nel
    catalogo: la risoluzione risponde ``MODEL_UNAVAILABLE`` (§19.4).
    """

    id: uuid.UUID
    kind: AProfile
    display_name: str
    version: str
    created_at: datetime
    updated_at: datetime
    binding: BindingDTO
    model: ModelInfoDTO | None


class SwitchProfileModelRequest(BaseModel):
    """Nuova versione del profilo con un modello diverso (B-02.1).

    ``expected_profile_version`` è l'optimistic lock: il valore
    ``version`` dell'ultima lettura del profilo. Se il profilo è
    cambiato nel frattempo la richiesta risponde 409, mai una
    sovrascrittura silenziosa. ``idempotency_key`` opzionale: il retry
    della stessa richiesta restituisce lo stesso esito senza duplicarlo;
    stessa chiave con payload diverso risponde 409. ``instructions``
    assente mantiene quelle della versione corrente.
    """

    model_name: str = Field(min_length=1, max_length=MAX_MODEL_NAME_LENGTH)
    parameters: dict[str, object] = Field(default_factory=dict)
    instructions: str | None = Field(default=None, max_length=MAX_INSTRUCTIONS_LENGTH)
    expected_profile_version: str | None = Field(
        default=None, min_length=1, max_length=MAX_PROFILE_VERSION_LENGTH
    )
    idempotency_key: str | None = Field(
        default=None, min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LENGTH
    )

    @field_validator("model_name")
    @classmethod
    def model_name_not_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("il valore non può contenere solo spazi")
        return v


class ProfileListDTO(BaseModel):
    """Profili propri: collezione piccola e finita, senza paginazione."""

    items: list[ProfileDTO]


class ModelListDTO(BaseModel):
    """Catalogo del runtime di inference locale (NewRay.md §9.1)."""

    items: list[ModelInfoDTO]


class ModelReadinessDTO(BaseModel):
    """Stato osservato del candidato; capacità dichiarate non qualificate."""

    state: ReadinessState
    model_name: str | None
    digest: str | None
    declared_capabilities: list[str]


class ResolvedBindingDTO(BaseModel):
    """Snapshot della risoluzione del binding: riproducibile (ADR 0003).

    Stesso profilo sullo stesso stato del catalogo → stesso payload.
    """

    profile_id: uuid.UUID
    profile_version_id: uuid.UUID
    binding_id: uuid.UUID
    profile_version: str
    binding_name: str
    runtime: str
    model_name: str
    digest: str
    parameters: dict[str, object]
    capabilities: list[str]
    instructions: str
