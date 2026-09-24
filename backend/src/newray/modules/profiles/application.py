"""Casi d'uso di profili e binding dei modelli (NewRay.md §§7.2, 8.1, 19.2; B-02).

Il principal è sempre risolto dal server: nessun dato del client
partecipa. La risoluzione del binding è esplicita e riproducibile
(ADR 0003): se il modello manca nel catalogo, l'esito è
``MODEL_UNAVAILABLE`` recuperabile, mai un fallback invisibile.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import replace

from newray.kernel.clock import Clock
from newray.kernel.errors import AccessDenied, ModelUnavailable, NotFound
from newray.kernel.identity import Principal, Scope, new_id
from newray.modules.models import (
    RUNTIME_OLLAMA,
    ModelCatalog,
    ModelInfo,
    ModelReadiness,
)

from .domain import (
    DEFAULT_BINDING_NAME,
    DEFAULT_PROFILE_VERSION,
    AProfile,
    ModelBinding,
    Profile,
    ProfileVersion,
    ProfileView,
    ResolvedBinding,
)
from .ports import (
    ModelBindingStore,
    ProfileDefaultsSeeder,
    ProfileRepository,
    ProfileVersionWriter,
)


def _bump_version(current: str) -> str:
    """Prossima versione del profilo: incrementa l'ultimo segmento numerico.

    ``"1.0.0"`` -> ``"1.0.1"``. Un formato senza segmento numerico finale
    riparte da un contatore aggiunto in coda: mai una riscrittura del
    valore esistente, la versione precedente resta quella della riga
    storica (§7.2).
    """
    parts = current.split(".")
    if parts and parts[-1].isdigit():
        parts[-1] = str(int(parts[-1]) + 1)
        return ".".join(parts)
    return f"{current}.1"


def _switch_request_hash(
    model_name: str,
    parameters: Mapping[str, object],
    instructions: str | None,
    expected_profile_version: str | None,
) -> str:
    """Hash del payload normalizzato di uno switch: stessa chiave di
    idempotenza con input diverso deve essere riconoscibile come conflitto."""
    canonical = json.dumps(
        {
            "model_name": model_name,
            "parameters": parameters,
            "instructions": instructions,
            "expected_profile_version": expected_profile_version,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_ASSISTANT_INSTRUCTIONS = (
    "Assistente di lavoro: risponde usando le fonti a disposizione "
    "e dichiara esplicitamente cosa non sa."
)


class ProfileService:
    """Casi d'uso: profili, modelli e risoluzione dei binding in scope."""

    def __init__(
        self,
        profiles: ProfileRepository,
        bindings: ModelBindingStore,
        catalog: ModelCatalog,
        clock: Clock,
        seeder: ProfileDefaultsSeeder,
        *,
        default_model_name: str | None = None,
        version_writer: ProfileVersionWriter | None = None,
    ) -> None:
        self._profiles = profiles
        self._bindings = bindings
        self._catalog = catalog
        self._clock = clock
        self._seeder = seeder
        self._default_model_name = default_model_name
        self._version_writer = version_writer

    def ensure_defaults(self, principal: Principal) -> None:
        """Provisioning atomico e idempotente del solo Assistente pilot.

        Una versione storica di Assistente resta intatta, anche se la
        configurazione del modello è cambiata. Il seeder ripara un profilo
        senza versione, senza provisionare gli specialisti legacy.
        """
        esistenti = self._profiles.list_profiles(principal.scope)
        if any(profile.kind is AProfile.ASSISTANT for profile, _ in esistenti):
            return
        if not self._default_model_name:
            raise ModelUnavailable("configurare NEWRAY_DEFAULT_MODEL_NAME per creare i profili")
        now = self._clock.now()
        organization_id, owner_id = principal.organization_id, principal.user_id
        binding = ModelBinding(
            id=new_id(),
            organization_id=organization_id,
            owner_id=owner_id,
            name=DEFAULT_BINDING_NAME,
            runtime=RUNTIME_OLLAMA,
            model_name=self._default_model_name,
            parameters={},
            created_at=now,
        )
        profile = Profile(
            id=new_id(),
            organization_id=organization_id,
            owner_id=owner_id,
            kind=AProfile.ASSISTANT,
            display_name="Assistente",
            created_at=now,
            updated_at=now,
        )
        version = ProfileVersion(
            id=new_id(),
            profile_id=profile.id,
            organization_id=organization_id,
            owner_id=owner_id,
            version=DEFAULT_PROFILE_VERSION,
            model_binding_id=binding.id,
            instructions=_ASSISTANT_INSTRUCTIONS,
            created_at=now,
        )
        self._seeder.execute(principal.scope, binding, [(profile, version)])

    async def provision_default(self, principal: Principal) -> ProfileView:
        """Crea o restituisce il default; tutta la persistenza resta offloadata."""
        stored = await asyncio.to_thread(self._provision_and_load, principal)
        return self._with_model(stored, await self._catalog.list_models())

    def _provision_and_load(self, principal: Principal) -> ProfileView:
        self.ensure_defaults(principal)
        for profile in self._profiles.list_profiles(principal.scope):
            if profile[0].kind is AProfile.ASSISTANT:
                return self._stored_view(profile)
        raise RuntimeError("Assistente mancante dopo il provisioning")

    async def list_profiles(self, principal: Principal) -> list[ProfileView]:
        """Profili propri con versione, binding e disponibilità del modello.

        La lettura non crea binding, profili o versioni.
        """
        views = await asyncio.to_thread(self._load_views, principal)
        models = await self._catalog.list_models()
        return [self._with_model(view, models) for view in views]

    def _load_views(self, principal: Principal) -> list[ProfileView]:
        """Blocco sincrono completo; nessun repository chiamato dal ciclo eventi."""
        return [self._stored_view(item) for item in self._profiles.list_profiles(principal.scope)]

    async def list_models(self) -> list[ModelInfo]:
        """Modelli del runtime di inference, dallo stato della macchina."""
        return await self._catalog.list_models()

    async def model_readiness(self) -> ModelReadiness:
        """Stato osservato del candidato configurato, senza mutazioni o load."""
        return await self._catalog.readiness(self._default_model_name)

    async def resolve_binding(
        self, principal: Principal, profile_id: uuid.UUID, *, assistant_only: bool = False
    ) -> ResolvedBinding:
        """Risoluzione esplicita del binding del profilo (ADR 0003).

        Produce lo snapshot riproducibile (versione profilo, binding,
        digest e capacità del modello). Fuori scope → ``NotFound``;
        modello assente dal catalogo → ``ModelUnavailable``.
        """
        stored = await asyncio.to_thread(self._load_view, principal, profile_id)
        if assistant_only and stored.profile.kind is not AProfile.ASSISTANT:
            raise AccessDenied("il pilot esegue soltanto il profilo Assistente")
        view = self._with_model(stored, await self._catalog.list_models())
        model = view.model
        if model is None:
            raise ModelUnavailable(
                f"modello {view.binding.model_name} non disponibile "
                f"nel runtime {view.binding.runtime}"
            )
        if not model.digest:
            raise ModelUnavailable(
                f"modello {view.binding.model_name} senza digest identificativo nel runtime"
            )
        return ResolvedBinding(
            profile_id=view.profile.id,
            profile_version_id=view.version.id,
            binding_id=view.binding.id,
            profile_version=view.version.version,
            binding_name=view.binding.name,
            runtime=view.binding.runtime,
            model_name=view.binding.model_name,
            digest=model.digest,
            parameters=view.binding.parameters,
            capabilities=model.capabilities,
            instructions=view.version.instructions,
        )

    async def switch_model(
        self,
        principal: Principal,
        profile_id: uuid.UUID,
        model_name: str,
        *,
        parameters: Mapping[str, object] | None = None,
        instructions: str | None = None,
        expected_profile_version: str | None = None,
        idempotency_key: str | None = None,
    ) -> ProfileView:
        """Nuova versione del profilo con un modello diverso (B-02.1).

        Crea sempre un binding e una versione nuovi, mai una modifica
        in-place (§7.2). Il modello richiesto deve essere disponibile e
        avere un digest nel catalogo del runtime, come ``resolve_binding``
        (ADR 0003) — altrimenti ``ModelUnavailable``. Profilo assente o
        fuori scope → ``NotFound``. ``expected_profile_version`` è
        l'optimistic lock: profilo cambiato dall'ultima lettura del
        client → ``Conflict``. ``idempotency_key`` rende sicuro il retry
        della stessa richiesta.
        """
        if self._version_writer is None:
            raise ModelUnavailable("switch_model richiede un ProfileVersionWriter configurato")
        stored = await asyncio.to_thread(self._load_view, principal, profile_id)
        resolved_parameters = dict(parameters or {})
        request_hash = (
            _switch_request_hash(
                model_name, resolved_parameters, instructions, expected_profile_version
            )
            if idempotency_key is not None
            else None
        )
        if idempotency_key is not None:
            assert request_hash is not None
            receipt = await asyncio.to_thread(
                self._version_writer.find_switch_receipt,
                principal.scope,
                profile_id,
                idempotency_key,
                request_hash,
            )
            if receipt is not None:
                saved_version, saved_binding = receipt
                return ProfileView(
                    profile=stored.profile,
                    version=saved_version,
                    binding=saved_binding,
                    model=None,
                )
        models = await self._catalog.list_models()
        model = next(
            (m for m in models if m.runtime == RUNTIME_OLLAMA and m.name == model_name), None
        )
        if model is None or not model.digest:
            raise ModelUnavailable(
                f"modello {model_name} non disponibile nel runtime {RUNTIME_OLLAMA}"
            )

        now = self._clock.now()
        resolved_instructions = (
            instructions if instructions is not None else stored.version.instructions
        )
        binding = ModelBinding(
            id=new_id(),
            organization_id=principal.organization_id,
            owner_id=principal.user_id,
            # Nome univoco per costruzione: ogni switch è un binding a
            # sé, mai una riscrittura di quello esistente.
            name=str(new_id()),
            runtime=RUNTIME_OLLAMA,
            model_name=model_name,
            parameters=resolved_parameters,
            created_at=now,
        )
        version = ProfileVersion(
            id=new_id(),
            profile_id=profile_id,
            organization_id=principal.organization_id,
            owner_id=principal.user_id,
            version=_bump_version(stored.version.version),
            model_binding_id=binding.id,
            instructions=resolved_instructions,
            created_at=now,
        )
        persisted_version, persisted_binding = await asyncio.to_thread(
            self._version_writer.switch_model,
            principal.scope,
            profile_id,
            binding,
            version,
            expected_profile_version=expected_profile_version,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        view = ProfileView(
            profile=stored.profile,
            version=persisted_version,
            binding=persisted_binding,
            model=None,
        )
        return self._with_model(view, models)

    def _load_view(self, principal: Principal, profile_id: uuid.UUID) -> ProfileView:
        item = self._profiles.get_profile(principal.scope, profile_id)
        if item is None:
            raise NotFound("profilo non trovato")
        return self._stored_view(item)

    def _stored_view(self, item: tuple[Profile, ProfileVersion]) -> ProfileView:
        profile, version = item
        # Lo scope deriva dalle righe stesse: lo schema garantisce che
        # binding e versione siano dello stesso proprietario.
        binding = self._bindings.get_binding(
            Scope(version.organization_id, version.owner_id),
            version.model_binding_id,
        )
        if binding is None:
            # Invariante dello schema (FK + seeding): se non è qui c'è
            # un difetto del server, non uno stato di dominio.
            raise RuntimeError("binding del profilo mancante dalla persistenza")
        return ProfileView(profile=profile, version=version, binding=binding, model=None)

    @staticmethod
    def _with_model(view: ProfileView, models: list[ModelInfo]) -> ProfileView:
        model = next(
            (
                m
                for m in models
                if m.runtime == view.binding.runtime and m.name == view.binding.model_name
            ),
            None,
        )
        return replace(view, model=model)
