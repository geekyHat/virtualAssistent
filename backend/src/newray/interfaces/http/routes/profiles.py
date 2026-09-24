"""Route profili e modelli (NewRay.md §19.2; B-02).

Il principal è sempre risolto dal server (cookie opaco, HttpOnly); i
profili e i binding sono accessibili solo nel proprio scope (§7.3):
fuori scope risponde 404 uniforme, senza rivelarne l'esistenza.

Il catalogo modelli è lo stato del runtime di inference locale (§9.1):
protetto dall'autenticazione, non dallo scope del principal.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from fastapi import APIRouter, Depends, Request

from newray.interfaces.http.dto.profiles import (
    BindingDTO,
    ModelInfoDTO,
    ModelListDTO,
    ModelReadinessDTO,
    ProfileDTO,
    ProfileListDTO,
    ResolvedBindingDTO,
    SwitchProfileModelRequest,
)
from newray.interfaces.http.middleware.identity import require_principal
from newray.interfaces.http.routes import API_PREFIX
from newray.kernel.identity import Principal
from newray.modules.models import ModelInfo
from newray.modules.profiles import ProfileService, ProfileView, ResolvedBinding


def _model_info(model: ModelInfo) -> ModelInfoDTO:
    return ModelInfoDTO(
        name=model.name,
        runtime=model.runtime,
        digest=model.digest,
        status=model.status,
        capabilities=list(model.capabilities),
    )


def _profile(view: ProfileView) -> ProfileDTO:
    return ProfileDTO(
        id=view.profile.id,
        kind=view.profile.kind,
        display_name=view.profile.display_name,
        version=view.version.version,
        created_at=view.profile.created_at,
        updated_at=view.profile.updated_at,
        binding=BindingDTO(
            name=view.binding.name,
            runtime=view.binding.runtime,
            model_name=view.binding.model_name,
            parameters=view.binding.parameters,
        ),
        model=_model_info(view.model) if view.model is not None else None,
    )


def _resolved(binding: ResolvedBinding) -> ResolvedBindingDTO:
    return ResolvedBindingDTO(
        profile_id=binding.profile_id,
        profile_version_id=binding.profile_version_id,
        binding_id=binding.binding_id,
        profile_version=binding.profile_version,
        binding_name=binding.binding_name,
        runtime=binding.runtime,
        model_name=binding.model_name,
        digest=binding.digest,
        parameters=_json_parameters(binding.parameters),
        capabilities=list(binding.capabilities),
        instructions=binding.instructions,
    )


def _json_parameters(value: Mapping[str, object]) -> dict[str, object]:
    """Converte la copia immutabile di dominio nel JSON del contratto HTTP."""
    return {key: _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def build_router() -> APIRouter:
    """Costruisce le route profili e modelli; i servizi sono nel state."""
    router = APIRouter(prefix=API_PREFIX)

    @router.get("/profiles", response_model=ProfileListDTO)
    async def list_profiles(
        request: Request, principal: Principal = Depends(require_principal)
    ) -> ProfileListDTO:
        """Profili propri con versione, binding e disponibilità del modello."""
        service: ProfileService = request.app.state.profile_service
        return ProfileListDTO(
            items=[_profile(view) for view in await service.list_profiles(principal)]
        )

    @router.post("/profiles/defaults", response_model=ProfileDTO)
    async def provision_default(
        request: Request, principal: Principal = Depends(require_principal)
    ) -> ProfileDTO:
        """Provisioning esplicito e idempotente del solo Assistente."""
        service: ProfileService = request.app.state.profile_service
        return _profile(await service.provision_default(principal))

    @router.get("/models", response_model=ModelListDTO)
    async def list_models(
        request: Request, principal: Principal = Depends(require_principal)
    ) -> ModelListDTO:
        """Catalogo dei modelli del runtime di inference locale (§9.1)."""
        service: ProfileService = request.app.state.profile_service
        return ModelListDTO(items=[_model_info(m) for m in await service.list_models()])

    @router.get("/models/readiness", response_model=ModelReadinessDTO)
    async def model_readiness(
        request: Request, principal: Principal = Depends(require_principal)
    ) -> ModelReadinessDTO:
        """Diagnostica autenticata e read-only del modello pilot configurato."""
        service: ProfileService = request.app.state.profile_service
        observed = await service.model_readiness()
        return ModelReadinessDTO(
            state=observed.state,
            model_name=observed.model_name,
            digest=observed.digest,
            declared_capabilities=list(observed.declared_capabilities),
        )

    @router.post("/profiles/{profile_id}/versions", status_code=201, response_model=ProfileDTO)
    async def switch_model(
        profile_id: uuid.UUID,
        request: Request,
        body: SwitchProfileModelRequest,
        principal: Principal = Depends(require_principal),
    ) -> ProfileDTO:
        """Nuova versione del profilo con un modello diverso (B-02.1).

        Crea sempre un binding e una versione nuovi, mai una modifica
        in-place. ``expected_profile_version`` obsoleta o chiave di
        idempotenza riusata con payload diverso → 409. Modello assente
        dal catalogo → 503 ``MODEL_UNAVAILABLE`` recuperabile.
        """
        service: ProfileService = request.app.state.profile_service
        view = await service.switch_model(
            principal,
            profile_id,
            body.model_name,
            parameters=body.parameters,
            instructions=body.instructions,
            expected_profile_version=body.expected_profile_version,
            idempotency_key=body.idempotency_key,
        )
        return _profile(view)

    @router.get("/profiles/{profile_id}/binding", response_model=ResolvedBindingDTO)
    async def resolve_binding(
        profile_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> ResolvedBindingDTO:
        """Snapshot riproducibile della risoluzione del binding (ADR 0003).

        Modello assente dal catalogo → 503 ``MODEL_UNAVAILABLE``
        recuperabile, mai un fallback invisibile.
        """
        service: ProfileService = request.app.state.profile_service
        return _resolved(await service.resolve_binding(principal, profile_id))

    return router
