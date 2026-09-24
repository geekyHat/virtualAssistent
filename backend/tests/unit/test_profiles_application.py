"""Casi d'uso di profili e binding dei modelli (NewRay.md §§7.2, 8.1, 9.1; B-02).

Fake: verificano il contratto (scope obbligatorio, seeding idempotente,
risoluzione riproducibile, ``MODEL_UNAVAILABLE`` recuperabile, mai un
fallback). La qualità live e la RLS reale sono provate in
``tests/integration`` su PostgreSQL (NewRay.md §22.2).
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from fakes import (
    FakeClock,
    InMemoryModelBindingStore,
    InMemoryModelCatalog,
    InMemoryProfileDefaultsSeeder,
    InMemoryProfileRepository,
    InMemoryProfileVersionWriter,
)
from newray.kernel.errors import AccessDenied, Conflict, ModelUnavailable, NotFound
from newray.kernel.identity import Principal, Role, new_id
from newray.modules.models import (
    DEFAULT_MODEL_NAME,
    RUNTIME_OLLAMA,
    ModelInfo,
    ModelStatus,
)
from newray.modules.profiles import (
    DEFAULT_BINDING_NAME,
    DEFAULT_PROFILE_VERSION,
    AProfile,
    ModelBinding,
    Profile,
    ProfileService,
    ProfileVersion,
    ProfileView,
)

START = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
ORG = new_id()

MODEL = ModelInfo(
    name=DEFAULT_MODEL_NAME,
    runtime=RUNTIME_OLLAMA,
    digest="sha256:abc123",
    status=ModelStatus.QUALIFIED,
    capabilities=("chat", "tools"),
)

OTHER_MODEL = ModelInfo(
    name="newray-gemma4-31b-it:ud-q4-k-xl",
    runtime=RUNTIME_OLLAMA,
    digest="sha256:def456",
    status=ModelStatus.INSTALLED,
    capabilities=("chat",),
)


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock(START)


@pytest.fixture()
def principal() -> Principal:
    return Principal(user_id=new_id(), organization_id=ORG, session_id=new_id(), role=Role.OWNER)


@pytest.fixture()
def other(principal: Principal) -> Principal:
    """Stessa organizzazione, utente diverso: il caso d'uso più comune da isolare."""
    return Principal(
        user_id=new_id(),
        organization_id=principal.organization_id,
        session_id=new_id(),
        role=Role.MEMBER,
    )


def _service(clock: FakeClock, catalog: InMemoryModelCatalog | None = None) -> ProfileService:
    repo = InMemoryProfileRepository()
    store = InMemoryModelBindingStore()
    return ProfileService(
        repo,
        store,
        catalog if catalog is not None else InMemoryModelCatalog(),
        clock,
        InMemoryProfileDefaultsSeeder(repo, store),
        default_model_name="llama3.1",
    )


def _seeded_list(service: ProfileService, principal: Principal) -> list[ProfileView]:
    service.ensure_defaults(principal)
    return asyncio.run(service.list_profiles(principal))


def test_lista_è_pura_e_provisioning_crea_solo_assistente(
    principal: Principal, clock: FakeClock
) -> None:
    service = _service(clock)
    assert asyncio.run(service.list_profiles(principal)) == []
    views = _seeded_list(service, principal)
    assert [view.profile.kind for view in views] == [AProfile.ASSISTANT]
    for view in views:
        assert view.profile.owner_id == principal.user_id
        assert view.version.version == DEFAULT_PROFILE_VERSION
        assert view.binding.name == DEFAULT_BINDING_NAME
        assert view.binding.runtime == RUNTIME_OLLAMA
        assert view.binding.model_name == DEFAULT_MODEL_NAME
        # Catalogo vuoto: la disponibilità del modello è esplicitamente None.
        assert view.model is None


def test_seeding_idempotente(principal: Principal, clock: FakeClock) -> None:
    service = _service(clock)
    first = _seeded_list(service, principal)
    service.ensure_defaults(principal)
    second = _seeded_list(service, principal)
    assert [view.profile.id for view in first] == [view.profile.id for view in second]
    assert len(second) == 1


def test_guasto_del_seeding_non_lascia_residui_e_il_retry_riesce(
    principal: Principal, clock: FakeClock
) -> None:
    """R03: il guasto durante il passo atomico propaga l'errore senza
    residui; il seeding ripetuto completa tutto."""
    repo = InMemoryProfileRepository()
    store = InMemoryModelBindingStore()
    interno = InMemoryProfileDefaultsSeeder(repo, store)
    instabile = _SeederInstabile(interno)
    service = ProfileService(
        repo, store, InMemoryModelCatalog(), clock, instabile, default_model_name="llama3.1"
    )
    with pytest.raises(RuntimeError, match="guasto"):
        service.ensure_defaults(principal)
    assert store.find_binding_by_name(principal.scope, DEFAULT_BINDING_NAME) is None
    assert repo.list_profiles(principal.scope) == []
    # Retry: il passo atomico riparte da zero e crea il solo Assistente.
    views = _seeded_list(service, principal)
    assert instabile.tentativi == 2
    assert len(views) == 1
    assert {v.binding.id for v in views} == {
        store.find_binding_by_name(principal.scope, DEFAULT_BINDING_NAME).id
    }


def test_seeding_ripara_lo_stato_parziale_solo_binding(
    principal: Principal, clock: FakeClock
) -> None:
    """R03: binding senza profili (guasto del vecchio seeding) → i profili
    vengono creati riusando il binding esistente, mai riscritto."""
    repo = InMemoryProfileRepository()
    store = InMemoryModelBindingStore()
    binding = ModelBinding(
        id=new_id(),
        organization_id=principal.organization_id,
        owner_id=principal.user_id,
        name=DEFAULT_BINDING_NAME,
        runtime=RUNTIME_OLLAMA,
        model_name="llama3.1",
        parameters={},
        created_at=clock.now(),
    )
    store.add_binding(binding)
    service = _service_con(repo, store, clock)
    views = _seeded_list(service, principal)
    assert len(views) == 1
    # Il binding parziale con nome corrente è riusato.
    assert {v.binding.id for v in views} == {binding.id}
    assert store.find_binding_by_name(principal.scope, DEFAULT_BINDING_NAME) is binding


def test_seeding_ripara_profilo_senza_versione(principal: Principal, clock: FakeClock) -> None:
    """R03: profilo creato ma senza versione → la versione manca e viene
    aggiunta, il profilo esiste già e non viene duplicato."""
    repo = InMemoryProfileRepository()
    store = InMemoryModelBindingStore()
    orphan = Profile(
        id=new_id(),
        organization_id=principal.organization_id,
        owner_id=principal.user_id,
        kind=AProfile.ASSISTANT,
        display_name="Assistente storico",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    repo.add_profile(orphan)
    service = _service_con(repo, store, clock)
    views = _seeded_list(service, principal)
    assert len(views) == 1
    assert views[0].profile.id == orphan.id
    assert views[0].profile.display_name == "Assistente storico"
    assert views[0].version.version == DEFAULT_PROFILE_VERSION


def test_binding_legacy_parziale_non_sceglie_il_modello_del_pilot(
    principal: Principal, clock: FakeClock
) -> None:
    repo = InMemoryProfileRepository()
    store = InMemoryModelBindingStore()
    legacy = ModelBinding(
        id=new_id(),
        organization_id=principal.organization_id,
        owner_id=principal.user_id,
        name="local-default",
        runtime=RUNTIME_OLLAMA,
        model_name="storico:q4",
        parameters={},
        created_at=clock.now(),
    )
    store.add_binding(legacy)
    service = ProfileService(
        repo,
        store,
        InMemoryModelCatalog(),
        clock,
        InMemoryProfileDefaultsSeeder(repo, store),
        default_model_name=OTHER_MODEL.name,
    )
    assistant = asyncio.run(service.provision_default(principal))
    assert assistant.binding.model_name == OTHER_MODEL.name
    assert assistant.binding.id != legacy.id
    assert store.find_binding_by_name(principal.scope, "local-default") == legacy


def test_profilo_storico_preservato_e_specialista_escluso_dal_run_pilot(
    principal: Principal, clock: FakeClock
) -> None:
    repo = InMemoryProfileRepository()
    store = InMemoryModelBindingStore()
    legacy = ModelBinding(
        id=new_id(),
        organization_id=principal.organization_id,
        owner_id=principal.user_id,
        name="local-default",
        runtime=RUNTIME_OLLAMA,
        model_name=MODEL.name,
        parameters={},
        created_at=clock.now(),
    )
    store.add_binding(legacy)
    old_ids: dict[AProfile, tuple[object, object]] = {}
    for kind in AProfile:
        profile = Profile(
            id=new_id(),
            organization_id=principal.organization_id,
            owner_id=principal.user_id,
            kind=kind,
            display_name=kind.value,
            created_at=clock.now(),
            updated_at=clock.now(),
        )
        version = ProfileVersion(
            id=new_id(),
            profile_id=profile.id,
            organization_id=principal.organization_id,
            owner_id=principal.user_id,
            version="1.0.0",
            model_binding_id=legacy.id,
            instructions="istruzioni storiche",
            created_at=clock.now(),
        )
        repo.add_profile(profile)
        repo.add_version(version)
        old_ids[kind] = (profile.id, version.id)
    service = ProfileService(
        repo,
        store,
        InMemoryModelCatalog((MODEL,)),
        clock,
        InMemoryProfileDefaultsSeeder(repo, store),
        default_model_name=OTHER_MODEL.name,
    )
    assistant = asyncio.run(service.provision_default(principal))
    views = asyncio.run(service.list_profiles(principal))
    assert len(views) == 3
    assert {view.profile.kind: (view.profile.id, view.version.id) for view in views} == old_ids
    assert assistant.binding.id == legacy.id
    assert assistant.binding.model_name == MODEL.name
    coder_id = next(view.profile.id for view in views if view.profile.kind is AProfile.CODER)
    with pytest.raises(AccessDenied):
        asyncio.run(service.resolve_binding(principal, coder_id, assistant_only=True))
    assert asyncio.run(service.resolve_binding(principal, coder_id)).binding_id == legacy.id


def _service_con(
    repo: InMemoryProfileRepository, store: InMemoryModelBindingStore, clock: FakeClock
) -> ProfileService:
    return ProfileService(
        repo,
        store,
        InMemoryModelCatalog(),
        clock,
        InMemoryProfileDefaultsSeeder(repo, store),
        default_model_name="llama3.1",
    )


class _SeederInstabile:
    """Porta di fault injection: fallisce al primo passo atomico, poi delega."""

    def __init__(self, interno: InMemoryProfileDefaultsSeeder) -> None:
        self._interno = interno
        self.tentativi = 0

    def execute(
        self, scope: object, binding: object, profiles: list[tuple[object, object]]
    ) -> None:
        self.tentativi += 1
        if self.tentativi == 1:
            raise RuntimeError("guasto simulato durante il seeding")
        self._interno.execute(scope, binding, profiles)  # type: ignore[arg-type]


def test_modello_configurato_non_riscrive_binding_esistenti(
    principal: Principal, clock: FakeClock
) -> None:
    repo = InMemoryProfileRepository()
    store = InMemoryModelBindingStore()
    catalog = InMemoryModelCatalog()
    service = ProfileService(
        repo,
        store,
        catalog,
        clock,
        InMemoryProfileDefaultsSeeder(repo, store),
        default_model_name="locale:q5",
    )
    first = _seeded_list(service, principal)
    assert {v.binding.model_name for v in first} == {"locale:q5"}
    restarted = ProfileService(
        repo,
        store,
        catalog,
        clock,
        InMemoryProfileDefaultsSeeder(repo, store),
        default_model_name="altro:q4",
    )
    second = _seeded_list(restarted, principal)
    assert {v.binding.id for v in second} == {v.binding.id for v in first}
    assert {v.binding.model_name for v in second} == {"locale:q5"}


def test_default_assente_non_crea_profili(principal: Principal, clock: FakeClock) -> None:
    repo = InMemoryProfileRepository()
    store = InMemoryModelBindingStore()
    service = ProfileService(
        repo, store, InMemoryModelCatalog(), clock, InMemoryProfileDefaultsSeeder(repo, store)
    )
    with pytest.raises(ModelUnavailable, match="NEWRAY_DEFAULT_MODEL_NAME"):
        service.ensure_defaults(principal)
    assert repo.list_profiles(principal.scope) == []


def test_profilo_altrui_non_è_rivelato(
    principal: Principal, other: Principal, clock: FakeClock
) -> None:
    service = _service(clock)
    mine = _seeded_list(service, principal)
    # B alla prima lettura crea i propri default: non vede i profili di A.
    theirs = _seeded_list(service, other)
    assert {v.profile.id for v in theirs}.isdisjoint({v.profile.id for v in mine})
    with pytest.raises(NotFound):
        asyncio.run(service.resolve_binding(other, mine[0].profile.id))


def test_lista_modelli_da_catalogo(clock: FakeClock) -> None:
    service = _service(clock, InMemoryModelCatalog((MODEL,)))
    assert asyncio.run(service.list_models()) == [MODEL]


def test_catalogo_letto_una_volta_per_operazione(principal: Principal, clock: FakeClock) -> None:
    catalog = InMemoryModelCatalog((MODEL,))
    catalog.list_models = AsyncMock(return_value=[MODEL])
    service = _service(clock, catalog)
    views = _seeded_list(service, principal)
    assert len(views) == 1
    catalog.list_models.assert_awaited_once()
    catalog.list_models.reset_mock()
    result = asyncio.run(service.resolve_binding(principal, views[0].profile.id))
    assert result.digest == MODEL.digest
    catalog.list_models.assert_awaited_once()


def test_lista_modelli_senza_runtime(clock: FakeClock) -> None:
    assert asyncio.run(_service(clock).list_models()) == []


def test_risoluzione_produce_snapshot_ripetibile(principal: Principal, clock: FakeClock) -> None:
    service = _service(clock, InMemoryModelCatalog((MODEL,)))
    profile = _seeded_list(service, principal)[0].profile
    first = asyncio.run(service.resolve_binding(principal, profile.id))
    second = asyncio.run(service.resolve_binding(principal, profile.id))
    assert first == second
    assert first.profile_version == DEFAULT_PROFILE_VERSION
    assert first.profile_version_id
    assert first.binding_id
    assert first.binding_name == DEFAULT_BINDING_NAME
    assert first.runtime == RUNTIME_OLLAMA
    assert first.model_name == DEFAULT_MODEL_NAME
    assert first.digest == MODEL.digest
    assert first.capabilities == MODEL.capabilities
    assert first.parameters == {}


def test_risoluzione_modello_mancante_è_model_unavailable(
    principal: Principal, clock: FakeClock
) -> None:
    service = _service(clock)
    profile = _seeded_list(service, principal)[0].profile
    with pytest.raises(ModelUnavailable) as excinfo:
        asyncio.run(service.resolve_binding(principal, profile.id))
    assert excinfo.value.code == "MODEL_UNAVAILABLE"


def test_risoluzione_profilo_assente_è_404(principal: Principal, clock: FakeClock) -> None:
    service = _service(clock, InMemoryModelCatalog((MODEL,)))
    with pytest.raises(NotFound):
        asyncio.run(service.resolve_binding(principal, new_id()))


def test_snapshot_è_immutable(principal: Principal, clock: FakeClock) -> None:
    service = _service(clock, InMemoryModelCatalog((MODEL,)))
    profile = _seeded_list(service, principal)[0].profile
    snapshot = asyncio.run(service.resolve_binding(principal, profile.id))
    with pytest.raises(FrozenInstanceError):
        snapshot.model_name = "altro"


def test_snapshot_copia_e_congela_parametri_annidati(
    principal: Principal, clock: FakeClock
) -> None:
    """Un run non condivide strutture mutabili con il binding di profilo."""
    repo = InMemoryProfileRepository()
    store = InMemoryModelBindingStore()
    service = ProfileService(
        repo,
        store,
        InMemoryModelCatalog((MODEL,)),
        clock,
        InMemoryProfileDefaultsSeeder(repo, store),
        default_model_name="llama3.1",
    )
    view = _seeded_list(service, principal)[0]
    original = {"sampling": {"stop": ["END"], "temperature": 0.2}}
    custom = ModelBinding(
        id=new_id(),
        organization_id=principal.organization_id,
        owner_id=principal.user_id,
        name="immutabile",
        runtime=RUNTIME_OLLAMA,
        model_name=MODEL.name,
        parameters=original,
        created_at=clock.now(),
    )
    store.add_binding(custom)
    clock.advance(timedelta(seconds=1))
    repo.add_version(
        ProfileVersion(
            id=new_id(),
            profile_id=view.profile.id,
            organization_id=principal.organization_id,
            owner_id=principal.user_id,
            version="1.0.1",
            model_binding_id=custom.id,
            instructions=view.version.instructions,
            created_at=clock.now(),
        )
    )

    snapshot = asyncio.run(service.resolve_binding(principal, view.profile.id))
    original["sampling"]["stop"].append("MUTATO")
    assert snapshot.parameters == {"sampling": {"stop": ("END",), "temperature": 0.2}}
    with pytest.raises(TypeError):
        snapshot.parameters["nuovo"] = True  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.parameters["sampling"]["nuovo"] = True  # type: ignore[index]


def test_risoluzione_rifiuta_modello_privo_di_digest(
    principal: Principal, clock: FakeClock
) -> None:
    no_digest = ModelInfo(
        name=DEFAULT_MODEL_NAME,
        runtime=RUNTIME_OLLAMA,
        digest=None,
        status=ModelStatus.INSTALLED,
        capabilities=("chat",),
    )
    service = _service(clock, InMemoryModelCatalog((no_digest,)))
    profile = _seeded_list(service, principal)[0].profile
    with pytest.raises(ModelUnavailable, match="digest"):
        asyncio.run(service.resolve_binding(principal, profile.id))


def test_lista_e_lettura_singola_concordano_sulla_versione_corrente(
    principal: Principal, clock: FakeClock
) -> None:
    """B-03.2-07: ``list_profiles``, ``get_profile`` e ``resolve_binding``
    devono selezionare la stessa versione corrente.

    Regressione: il precedente adapter Postgres restituiva la versione
    più vecchia in ``get_profile`` (``rows[0]`` con ORDER BY crescente)
    mentre ``list_profiles`` deduplicava in Python prendendo la massima.
    Le tre letture divergevano fra loro.
    """
    repo = InMemoryProfileRepository()
    store = InMemoryModelBindingStore()
    service = ProfileService(
        repo,
        store,
        InMemoryModelCatalog((MODEL,)),
        clock,
        InMemoryProfileDefaultsSeeder(repo, store),
        default_model_name="llama3.1",
    )
    view = _seeded_list(service, principal)[0]

    # Nuova versione più recente con parametri distinti.
    custom = ModelBinding(
        id=new_id(),
        organization_id=principal.organization_id,
        owner_id=principal.user_id,
        name="personalizzato",
        runtime=RUNTIME_OLLAMA,
        model_name=MODEL.name,
        parameters={"temperature": 0.7},
        created_at=clock.now(),
    )
    store.add_binding(custom)
    clock.advance(timedelta(seconds=5))
    nuova = ProfileVersion(
        id=new_id(),
        profile_id=view.profile.id,
        organization_id=principal.organization_id,
        owner_id=principal.user_id,
        version="1.1.0",
        model_binding_id=custom.id,
        instructions=view.version.instructions,
        created_at=clock.now(),
    )
    repo.add_version(nuova)

    # Le tre letture concordano sulla stessa versione ("1.1.0").
    dalla_lista = next(
        v for v in _seeded_list(service, principal) if v.profile.id == view.profile.id
    )
    (_, dalla_get) = repo.get_profile(principal.scope, view.profile.id)  # type: ignore[misc]
    dallo_snapshot = asyncio.run(service.resolve_binding(principal, view.profile.id))

    assert dalla_lista.version.version == "1.1.0"
    assert dalla_get.version == "1.1.0"
    assert dallo_snapshot.profile_version == "1.1.0"

    # ID coerenti: parliamo tutti della stessa riga di database.
    assert dalla_lista.version.id == nuova.id
    assert dalla_get.id == nuova.id


def test_pareggio_timestamp_risolto_con_id_come_tiebreaker(
    principal: Principal, clock: FakeClock
) -> None:
    """B-03.2-07: due versioni con lo stesso ``created_at`` restano ordinabili.

    L'ordine deve essere deterministico anche a pareggio temporale — se
    l'orologio ha risoluzione insufficiente, l'``id`` fa da tiebreaker.
    ``list`` e ``get`` devono concordare sullo stesso vincitore.
    """
    repo = InMemoryProfileRepository()
    store = InMemoryModelBindingStore()
    service = ProfileService(
        repo,
        store,
        InMemoryModelCatalog((MODEL,)),
        clock,
        InMemoryProfileDefaultsSeeder(repo, store),
        default_model_name="llama3.1",
    )
    view = _seeded_list(service, principal)[0]

    # Due versioni con created_at identico al primo microsecondo.
    stesso_momento = clock.now()
    ids = sorted([new_id(), new_id()])  # deterministico rispetto al maggiore
    for identificatore in ids:
        repo.add_version(
            ProfileVersion(
                id=identificatore,
                profile_id=view.profile.id,
                organization_id=principal.organization_id,
                owner_id=principal.user_id,
                version=f"1.0.{identificatore.int % 100}",
                model_binding_id=view.binding.id,
                instructions=view.version.instructions,
                created_at=stesso_momento,
            )
        )

    (_, corrente) = repo.get_profile(principal.scope, view.profile.id)  # type: ignore[misc]
    dalla_lista = next(
        v for v in _seeded_list(service, principal) if v.profile.id == view.profile.id
    )

    # Con pareggio temporale, vince l'``id`` maggiore (stesso contratto del
    # ``DISTINCT ON ... ORDER BY id, created_at DESC, v.id DESC`` Postgres).
    assert corrente.id == max(ids + [view.version.id])
    assert dalla_lista.version.id == corrente.id


def test_versione_piu_recente_vince_con_parametri(principal: Principal, clock: FakeClock) -> None:
    """Una nuova versione del profilo fa risolvere il binding aggiornato."""
    repo = InMemoryProfileRepository()
    store = InMemoryModelBindingStore()
    service = ProfileService(
        repo,
        store,
        InMemoryModelCatalog((MODEL,)),
        clock,
        InMemoryProfileDefaultsSeeder(repo, store),
        default_model_name="llama3.1",
    )
    view = _seeded_list(service, principal)[0]
    custom = ModelBinding(
        id=new_id(),
        organization_id=principal.organization_id,
        owner_id=principal.user_id,
        name="personalizzato",
        runtime=RUNTIME_OLLAMA,
        model_name=MODEL.name,
        parameters={"temperature": 0.2},
        created_at=clock.now(),
    )
    store.add_binding(custom)
    # Il tempo avanza: la versione "più recente" ha un created_at proprio,
    # altrimenti il tiebreak è arbitrario (stesso contratto dell'adapter).
    clock.advance(timedelta(seconds=1))
    repo.add_version(
        ProfileVersion(
            id=new_id(),
            profile_id=view.profile.id,
            organization_id=principal.organization_id,
            owner_id=principal.user_id,
            version="1.0.1",
            model_binding_id=custom.id,
            instructions=view.version.instructions,
            created_at=clock.now(),
        )
    )
    snapshot = asyncio.run(service.resolve_binding(principal, view.profile.id))
    assert snapshot.profile_version == "1.0.1"
    assert snapshot.binding_name == "personalizzato"
    assert snapshot.parameters == {"temperature": 0.2}


def test_model_info_rifiuta_runtime_sconosciuto() -> None:
    with pytest.raises(ValueError):
        ModelInfo(
            name="x",
            runtime="openai",
            digest=None,
            status=ModelStatus.INSTALLED,
            capabilities=(),
        )


def test_model_binding_rifiuta_nome_fuori_limi() -> None:
    with pytest.raises(ValueError):
        ModelBinding(
            id=new_id(),
            organization_id=ORG,
            owner_id=new_id(),
            name="x" * 101,
            runtime=RUNTIME_OLLAMA,
            model_name="llama3.1",
            parameters={},
            created_at=START,
        )


def _service_switch(
    clock: FakeClock, catalog: InMemoryModelCatalog
) -> tuple[ProfileService, InMemoryProfileRepository, InMemoryModelBindingStore]:
    repo = InMemoryProfileRepository()
    store = InMemoryModelBindingStore()
    service = ProfileService(
        repo,
        store,
        catalog,
        clock,
        InMemoryProfileDefaultsSeeder(repo, store),
        default_model_name="llama3.1",
        version_writer=InMemoryProfileVersionWriter(repo, store),
    )
    return service, repo, store


def test_switch_model_crea_binding_e_versione_nuovi(principal: Principal, clock: FakeClock) -> None:
    service, _repo, _store = _service_switch(clock, InMemoryModelCatalog((MODEL, OTHER_MODEL)))
    profile = _seeded_list(service, principal)[0].profile
    # Il clock avanza: la nuova versione ha un created_at proprio, altrimenti
    # il tiebreak sull'id è arbitrario (stesso contratto dell'adapter, vedi
    # test_pareggio_timestamp_risolto_con_id_come_tiebreaker).
    clock.advance(timedelta(seconds=1))
    view = asyncio.run(
        service.switch_model(
            principal,
            profile.id,
            OTHER_MODEL.name,
            expected_profile_version=DEFAULT_PROFILE_VERSION,
        )
    )
    assert view.binding.model_name == OTHER_MODEL.name
    assert view.version.version != DEFAULT_PROFILE_VERSION
    assert view.model is not None and view.model.name == OTHER_MODEL.name
    # La lettura successiva concorda: la nuova versione è quella corrente.
    reloaded = _seeded_list(service, principal)
    current = next(v for v in reloaded if v.profile.id == profile.id)
    assert current.version.id == view.version.id
    assert current.binding.model_name == OTHER_MODEL.name


def test_switch_model_mantiene_le_istruzioni_se_non_specificate(
    principal: Principal, clock: FakeClock
) -> None:
    service, _repo, _store = _service_switch(clock, InMemoryModelCatalog((MODEL, OTHER_MODEL)))
    before = _seeded_list(service, principal)[0]
    clock.advance(timedelta(seconds=1))
    view = asyncio.run(
        service.switch_model(
            principal,
            before.profile.id,
            OTHER_MODEL.name,
            expected_profile_version=DEFAULT_PROFILE_VERSION,
        )
    )
    assert view.version.instructions == before.version.instructions


def test_switch_model_modello_assente_dal_catalogo_è_model_unavailable(
    principal: Principal, clock: FakeClock
) -> None:
    service, _repo, _store = _service_switch(clock, InMemoryModelCatalog((MODEL,)))
    profile = _seeded_list(service, principal)[0].profile
    with pytest.raises(ModelUnavailable):
        asyncio.run(service.switch_model(principal, profile.id, "modello-inesistente"))


def test_switch_model_rifiuta_modello_privo_di_digest(
    principal: Principal, clock: FakeClock
) -> None:
    no_digest = ModelInfo(
        name="senza-digest",
        runtime=RUNTIME_OLLAMA,
        digest=None,
        status=ModelStatus.INSTALLED,
        capabilities=(),
    )
    service, _repo, _store = _service_switch(clock, InMemoryModelCatalog((MODEL, no_digest)))
    profile = _seeded_list(service, principal)[0].profile
    with pytest.raises(ModelUnavailable):
        asyncio.run(service.switch_model(principal, profile.id, no_digest.name))


def test_switch_model_versione_attesa_obsoleta_è_conflict(
    principal: Principal, clock: FakeClock
) -> None:
    service, _repo, _store = _service_switch(clock, InMemoryModelCatalog((MODEL, OTHER_MODEL)))
    profile = _seeded_list(service, principal)[0].profile
    with pytest.raises(Conflict):
        asyncio.run(
            service.switch_model(
                principal, profile.id, OTHER_MODEL.name, expected_profile_version="9.9.9"
            )
        )


def test_switch_model_due_scritture_concorrenti_sulla_stessa_versione_attesa(
    principal: Principal, clock: FakeClock
) -> None:
    """Due letture stale che tentano lo switch con la stessa versione
    attesa: la prima vince, la seconda trova lo stato già cambiato."""
    service, _repo, _store = _service_switch(clock, InMemoryModelCatalog((MODEL, OTHER_MODEL)))
    profile = _seeded_list(service, principal)[0].profile
    clock.advance(timedelta(seconds=1))
    asyncio.run(
        service.switch_model(
            principal,
            profile.id,
            OTHER_MODEL.name,
            expected_profile_version=DEFAULT_PROFILE_VERSION,
        )
    )
    with pytest.raises(Conflict):
        asyncio.run(
            service.switch_model(
                principal,
                profile.id,
                MODEL.name,
                expected_profile_version=DEFAULT_PROFILE_VERSION,
            )
        )


def test_switch_model_idempotenza_replica_lo_stesso_esito(
    principal: Principal, clock: FakeClock
) -> None:
    service, _repo, _store = _service_switch(clock, InMemoryModelCatalog((MODEL, OTHER_MODEL)))
    profile = _seeded_list(service, principal)[0].profile
    clock.advance(timedelta(seconds=1))
    first = asyncio.run(
        service.switch_model(
            principal,
            profile.id,
            OTHER_MODEL.name,
            expected_profile_version=DEFAULT_PROFILE_VERSION,
            idempotency_key="retry-1",
        )
    )
    second = asyncio.run(
        service.switch_model(
            principal,
            profile.id,
            OTHER_MODEL.name,
            expected_profile_version=DEFAULT_PROFILE_VERSION,
            idempotency_key="retry-1",
        )
    )
    assert first.version.id == second.version.id
    assert first.binding.id == second.binding.id


def test_switch_model_replay_non_dipende_da_istruzioni_o_catalogo_successivi(
    principal: Principal, clock: FakeClock
) -> None:
    catalog = InMemoryModelCatalog((MODEL, OTHER_MODEL))
    service, _repo, _store = _service_switch(clock, catalog)
    profile = _seeded_list(service, principal)[0].profile
    clock.advance(timedelta(seconds=1))
    original = asyncio.run(
        service.switch_model(
            principal,
            profile.id,
            OTHER_MODEL.name,
            expected_profile_version=DEFAULT_PROFILE_VERSION,
            idempotency_key="original",
        )
    )
    clock.advance(timedelta(seconds=1))
    asyncio.run(
        service.switch_model(
            principal,
            profile.id,
            MODEL.name,
            instructions="istruzioni cambiate",
            expected_profile_version=original.version.version,
            idempotency_key="successivo",
        )
    )
    catalog._models.clear()  # noqa: SLF001 — il runtime cambia dopo la ricevuta
    replay = asyncio.run(
        service.switch_model(
            principal,
            profile.id,
            OTHER_MODEL.name,
            expected_profile_version=DEFAULT_PROFILE_VERSION,
            idempotency_key="original",
        )
    )
    assert replay.version.id == original.version.id
    assert replay.binding.id == original.binding.id
    assert replay.model is None


def test_switch_model_chiave_di_idempotenza_riusata_con_payload_diverso_è_conflict(
    principal: Principal, clock: FakeClock
) -> None:
    service, _repo, _store = _service_switch(clock, InMemoryModelCatalog((MODEL, OTHER_MODEL)))
    profile = _seeded_list(service, principal)[0].profile
    clock.advance(timedelta(seconds=1))
    asyncio.run(
        service.switch_model(
            principal,
            profile.id,
            OTHER_MODEL.name,
            expected_profile_version=DEFAULT_PROFILE_VERSION,
            idempotency_key="retry-2",
        )
    )
    with pytest.raises(Conflict):
        asyncio.run(
            service.switch_model(
                principal,
                profile.id,
                MODEL.name,
                idempotency_key="retry-2",
            )
        )


def test_switch_model_profilo_altrui_è_not_found(
    principal: Principal, other: Principal, clock: FakeClock
) -> None:
    service, _repo, _store = _service_switch(clock, InMemoryModelCatalog((MODEL, OTHER_MODEL)))
    mine = _seeded_list(service, principal)[0].profile
    with pytest.raises(NotFound):
        asyncio.run(service.switch_model(other, mine.id, OTHER_MODEL.name))


def test_switch_model_senza_writer_configurato_è_model_unavailable(
    principal: Principal, clock: FakeClock
) -> None:
    service = _service(clock, InMemoryModelCatalog((MODEL, OTHER_MODEL)))
    profile = _seeded_list(service, principal)[0].profile
    with pytest.raises(ModelUnavailable):
        asyncio.run(service.switch_model(principal, profile.id, OTHER_MODEL.name))
