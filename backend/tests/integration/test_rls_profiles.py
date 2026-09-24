"""RLS profili, versioni e binding su PostgreSQL reale (NewRay.md §§7.3, 22.2; B-02).

Verifica la seconda barriera con il ruolo applicativo reale
(``newray_app``, senza superuser/BYPASSRLS): due utenti della stessa
organizzazione, un'organizzazione esterna, il proprietario delle tabelle
(FORCE) e la barriera dello schema contro scritture cross-principal.
Niente fake: questi test identificano schema e ruoli reali.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from conftest import DatabaseHandles
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from fakes import InMemoryModelCatalog
from newray.infrastructure.database import create_engine as create_db_engine
from newray.kernel.clock import SystemClock
from newray.kernel.identity import Principal, Role, Scope, new_id
from newray.modules.identity import IdentityService
from newray.modules.identity.adapters.postgres import (
    PostgresOwnerBootstrap,
    PostgresSessionStore,
    PostgresUserRepository,
)
from newray.modules.identity.domain import Organization, User
from newray.modules.models import (
    RUNTIME_OLLAMA,
    ModelInfo,
    ModelStatus,
)
from newray.modules.models.adapters.empty import EmptyModelCatalog
from newray.modules.profiles import (
    DEFAULT_BINDING_NAME,
    AProfile,
    ModelBinding,
    Profile,
    ProfileService,
    ProfileVersion,
    ProfileView,
)
from newray.modules.profiles.adapters.postgres import (
    PostgresModelBindingStore,
    PostgresProfileDefaultsSeeder,
    PostgresProfileRepository,
    PostgresProfileVersionWriter,
    _set_scope_context,
)

TABLES = ("profiles", "profile_versions", "model_bindings")


def _query(dsn: str, sql: str, params: dict | None = None) -> list[tuple]:
    with create_engine(dsn).connect() as conn:
        return [tuple(row) for row in conn.execute(text(sql), params or {})]


def _as_principal(dsn: str, scope: Scope, sql: str, params: dict | None = None) -> list[tuple]:
    """Esegue la query nel contesto del principal (transazione con GUC)."""
    with create_engine(dsn).connect() as conn:
        with conn.begin():
            conn.execute(
                text("SELECT set_config('app.user_id', :v, true)"),
                {"v": str(scope.user_id)},
            )
            conn.execute(
                text("SELECT set_config('app.organization_id', :v, true)"),
                {"v": str(scope.organization_id)},
            )
            return [tuple(row) for row in conn.execute(text(sql), params or {})]


def _now() -> datetime:
    return datetime.now(UTC)


def _seeded_list(service: ProfileService, principal: Principal) -> list[ProfileView]:
    service.ensure_defaults(principal)
    return asyncio.run(service.list_profiles(principal))


@pytest.fixture()
def setup(test_databases: DatabaseHandles):
    """Servizi sul database fresco; owner A e member B nella stessa organizzazione."""
    engine = create_db_engine(test_databases.app)
    identity = IdentityService(
        PostgresUserRepository(engine),
        PostgresSessionStore(engine),
        SystemClock(),
        PostgresOwnerBootstrap(engine),
    )
    profiles = ProfileService(
        PostgresProfileRepository(engine),
        PostgresModelBindingStore(engine),
        EmptyModelCatalog(),
        SystemClock(),
        PostgresProfileDefaultsSeeder(engine),
        default_model_name="llama3.1",
    )
    owner = identity.bootstrap_owner("A", "test-passphrase-1234")
    b = User(new_id(), owner.organization_id, "B", Role.MEMBER, _now())
    assert PostgresUserRepository(engine).add_user(b) is True
    session_b = identity.create_session(b)
    principal_b = Principal(b.id, owner.organization_id, session_b.id, Role.MEMBER)
    yield test_databases, engine, profiles, owner, principal_b
    engine.dispose()


def test_due_principal_stessa_organizzazione_sono_isolati(setup) -> None:
    dbs, _, profiles, owner, b = setup
    scope_a = Scope(owner.organization_id, owner.user_id)
    scope_b = Scope(owner.organization_id, b.user_id)

    a_views = _seeded_list(profiles, owner)
    b_views = _seeded_list(profiles, b)
    assert len(a_views) == 1 and len(b_views) == 1
    assert {v.profile.id for v in b_views}.isdisjoint({v.profile.id for v in a_views})

    # Con il ruolo applicativo reale: B conta i propri dati e non vede
    # mai quelli di A (NewRay.md §7.3).
    assert _as_principal(dbs.app, scope_b, "SELECT count(*) FROM profiles") == [(1,)]
    assert _as_principal(dbs.app, scope_b, "SELECT count(*) FROM profile_versions") == [(1,)]
    assert _as_principal(dbs.app, scope_b, "SELECT count(*) FROM model_bindings") == [(1,)]
    a_view = a_views[0]
    for table, row_id in (
        ("profiles", a_view.profile.id),
        ("profile_versions", a_view.version.id),
        ("model_bindings", a_view.binding.id),
    ):
        rows = _as_principal(
            dbs.app,
            scope_b,
            f"SELECT count(*) FROM {table} WHERE id = :id",
            {"id": str(row_id)},
        )
        assert rows == [(0,)]
    a_profile_id = a_view.profile.id

    # I repository, pur con il ruolo applicativo, non attraversano lo scope.
    repo = PostgresProfileRepository(create_db_engine(dbs.app))
    assert repo.get_profile(scope_b, a_profile_id) is None
    assert repo.get_profile(scope_a, a_profile_id) is not None


def test_senza_contesto_nessun_dato(setup) -> None:
    dbs, _, profiles, owner, _ = setup
    _seeded_list(profiles, owner)
    # GUC assenti → policy non soddisfatta: default deny (NewRay.md §7.3).
    for table in TABLES:
        assert _query(dbs.app, f"SELECT count(*) FROM {table}") == [(0,)]


def test_proprietario_tabelle_non_elude_rls(setup) -> None:
    """FORCE ROW LEVEL SECURITY: vale anche per il ruolo delle migrazioni."""
    dbs, _, profiles, owner, _ = setup
    _seeded_list(profiles, owner)
    for table in TABLES:
        assert _query(dbs.migration, f"SELECT count(*) FROM {table}") == [(0,)]


def test_organizzazione_esterna_non_legge(setup) -> None:
    dbs, engine, profiles, owner, _ = setup
    org2 = Organization(new_id(), "Altro", _now())
    identity = IdentityService(
        PostgresUserRepository(engine),
        PostgresSessionStore(engine),
        SystemClock(),
        PostgresOwnerBootstrap(engine),
    )
    repo = PostgresUserRepository(engine)
    c = User(new_id(), org2.id, "C", Role.MEMBER, _now())
    repo.add_organization(Scope(org2.id, c.id), org2)
    assert repo.add_user(c) is True

    a_views = _seeded_list(profiles, owner)
    session_c = identity.create_session(c)
    principal_c = Principal(c.id, org2.id, session_c.id, Role.MEMBER)
    c_views = _seeded_list(profiles, principal_c)
    assert len(c_views) == 1
    scope_c = Scope(org2.id, c.id)
    for table in TABLES:
        assert _as_principal(dbs.app, scope_c, f"SELECT count(*) FROM {table}") == [
            (1,),
        ]
    rows = _as_principal(
        dbs.app,
        scope_c,
        "SELECT count(*) FROM profiles WHERE id = :id",
        {"id": str(a_views[0].profile.id)},
    )
    assert rows == [(0,)]
    assert PostgresProfileRepository(engine).get_profile(scope_c, a_views[0].profile.id) is None


def test_un_solo_profilo_per_tipo_e_proprietario(setup) -> None:
    """Il seeding idempotente si appoggia al vincolo, non alla speranza."""
    _, engine, profiles, owner, _ = setup
    _seeded_list(profiles, owner)
    duplicate = Profile(
        id=new_id(),
        organization_id=owner.organization_id,
        owner_id=owner.user_id,
        kind=AProfile.ASSISTANT,
        display_name="Duplicato",
        created_at=_now(),
        updated_at=_now(),
    )
    with pytest.raises(IntegrityError):
        PostgresProfileRepository(engine).add_profile(duplicate)


def test_profili_e_versioni_legacy_restano_intatti_dopo_provisioning_pilot(setup) -> None:
    dbs, engine, _profiles, owner, _ = setup
    scope = Scope(owner.organization_id, owner.user_id)
    legacy = ModelBinding(
        id=new_id(),
        organization_id=scope.organization_id,
        owner_id=scope.user_id,
        name="local-default",
        runtime=RUNTIME_OLLAMA,
        model_name="storico:q4",
        parameters={},
        created_at=_now(),
    )
    PostgresModelBindingStore(engine).add_binding(legacy)
    repository = PostgresProfileRepository(engine)
    originals: dict[AProfile, tuple[object, object]] = {}
    for kind in AProfile:
        profile = Profile(
            id=new_id(),
            organization_id=scope.organization_id,
            owner_id=scope.user_id,
            kind=kind,
            display_name=kind.value,
            created_at=_now(),
            updated_at=_now(),
        )
        version = ProfileVersion(
            id=new_id(),
            profile_id=profile.id,
            organization_id=scope.organization_id,
            owner_id=scope.user_id,
            version="1.0.0",
            model_binding_id=legacy.id,
            instructions="storiche",
            created_at=_now(),
        )
        repository.add_profile(profile)
        repository.add_version(version)
        originals[kind] = (profile.id, version.id)

    pilot = ProfileService(
        repository,
        PostgresModelBindingStore(engine),
        EmptyModelCatalog(),
        SystemClock(),
        PostgresProfileDefaultsSeeder(engine),
        default_model_name="gemma-pilot:q4",
    )
    assistant = asyncio.run(pilot.provision_default(owner))
    assert assistant.binding.id == legacy.id
    assert assistant.binding.model_name == "storico:q4"
    views = asyncio.run(pilot.list_profiles(owner))
    assert {view.profile.kind: (view.profile.id, view.version.id) for view in views} == originals
    assert _as_principal(dbs.app, scope, "SELECT count(*) FROM model_bindings") == [(1,)]


def test_binding_legacy_parziale_non_viene_riusato_per_assistente_pilot(setup) -> None:
    dbs, engine, _profiles, owner, _ = setup
    scope = Scope(owner.organization_id, owner.user_id)
    old = ModelBinding(
        id=new_id(),
        organization_id=scope.organization_id,
        owner_id=scope.user_id,
        name="local-default",
        runtime=RUNTIME_OLLAMA,
        model_name="storico:q4",
        parameters={},
        created_at=_now(),
    )
    PostgresModelBindingStore(engine).add_binding(old)
    pilot = ProfileService(
        PostgresProfileRepository(engine),
        PostgresModelBindingStore(engine),
        EmptyModelCatalog(),
        SystemClock(),
        PostgresProfileDefaultsSeeder(engine),
        default_model_name="gemma-pilot:q4",
    )
    assistant = asyncio.run(pilot.provision_default(owner))
    assert assistant.binding.name == DEFAULT_BINDING_NAME
    assert assistant.binding.model_name == "gemma-pilot:q4"
    assert assistant.binding.id != old.id
    assert _as_principal(dbs.app, scope, "SELECT count(*) FROM model_bindings") == [(2,)]


def test_versione_in_profilo_altrui_bloccata_dallo_schema(setup) -> None:
    """Il proprietario della versione deve essere il proprietario del
    profilo: FK composito (migration 0003)."""
    _, engine, profiles, owner, b = setup
    a_view = _seeded_list(profiles, owner)[0]
    b_view = _seeded_list(profiles, b)[0]
    invasion = ProfileVersion(
        id=new_id(),
        profile_id=a_view.profile.id,
        organization_id=owner.organization_id,
        owner_id=b.user_id,
        version="2.0.0",
        model_binding_id=b_view.binding.id,
        instructions="invasione",
        created_at=_now(),
    )
    with pytest.raises(IntegrityError):
        PostgresProfileRepository(engine).add_version(invasion)


def test_due_seeding_concorrenti_producono_stato_completo(setup) -> None:
    """R03/P-02: due POST concorrenti danno un solo Assistente completo.

    ``ProfileService.provision_default`` usa la porta atomica con
    INSERT ... ON CONFLICT DO NOTHING + read-back. Il GET resta puro.
    """
    dbs, engine, _, owner, _ = setup

    # Ambiente pulito prima della gara: azzeriamo lo scope dell'owner
    # (il ``setup`` non chiama seeding). Verifica prima della corsa.
    scope_owner = Scope(owner.organization_id, owner.user_id)
    for table in TABLES:
        assert _as_principal(dbs.app, scope_owner, f"SELECT count(*) FROM {table}") == [(0,)]

    # Due servizi indipendenti (engine e store distinti) → nessun stato
    # condiviso a livello di applicazione: la sincronizzazione può passare
    # soltanto dalla porta atomica del seeder.
    def build_service() -> ProfileService:
        e = create_db_engine(dbs.app)
        return ProfileService(
            PostgresProfileRepository(e),
            PostgresModelBindingStore(e),
            EmptyModelCatalog(),
            SystemClock(),
            PostgresProfileDefaultsSeeder(e),
            default_model_name="llama3.1",
        )

    barriera = threading.Barrier(2)
    risultati: list[list | Exception] = []

    def gara() -> None:
        servizio = build_service()
        barriera.wait()
        try:
            risultati.append([asyncio.run(servizio.provision_default(owner))])
        except Exception as exc:  # noqa: BLE001 — voglio registrare qualsiasi errore
            risultati.append(exc)

    threads = [threading.Thread(target=gara) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Nessuna eccezione: la porta ha assorbito la corsa senza 500.
    for r in risultati:
        assert not isinstance(r, Exception), r
    # Entrambe le richieste vedono lo stesso Assistente.
    assert [len(r) for r in risultati] == [1, 1]
    for viste in risultati:
        assert {v.profile.kind for v in viste} == {AProfile.ASSISTANT}

    # Persistenza (superuser): niente duplicati sul cluster.
    assert _query(dbs.admin, "SELECT count(*) FROM model_bindings") == [(1,)]
    assert _query(dbs.admin, "SELECT count(*) FROM profiles") == [(1,)]
    assert _query(dbs.admin, "SELECT count(*) FROM profile_versions") == [(1,)]

    # La versione referenzia il binding vincente letto dal read-back.
    binding_ids = _query(dbs.admin, "SELECT DISTINCT model_binding_id FROM profile_versions")
    assert len(binding_ids) == 1

    # Il binding è quello canonico (nome DEFAULT_BINDING_NAME, runtime Ollama):
    # nessun binding sintetico o duplicato che condivide il nome.
    binding_rows = _query(
        dbs.admin,
        "SELECT name, runtime, model_name FROM model_bindings",
    )
    assert binding_rows == [(DEFAULT_BINDING_NAME, RUNTIME_OLLAMA, "llama3.1")]

    # ID coerenti fra le due viste: entrambe hanno visto lo stesso stato
    # dopo il commit atomico (i profili sono gli stessi, non due copie).
    prime, seconde = risultati
    per_kind_prime = {v.profile.kind: v.profile.id for v in prime}
    per_kind_seconde = {v.profile.kind: v.profile.id for v in seconde}
    assert per_kind_prime == per_kind_seconde


def test_seeding_ripara_binding_parziale_su_postgres(setup) -> None:
    """B-03.2-03: un binding senza profili viene completato al passaggio.

    Riproduce lo stato lasciato da un guasto passato del seeder: il
    binding di default esiste, ma nessun profilo. Il servizio deve
    riconoscerlo come parziale e completarlo, non ripartire da zero
    creando un secondo binding.
    """
    dbs, engine, profiles, owner, _ = setup

    # Stato parziale iniettato: solo il binding, con lo stesso nome
    # canonico del seeding di default.
    now = _now()
    orphaned = ModelBinding(
        id=new_id(),
        organization_id=owner.organization_id,
        owner_id=owner.user_id,
        name=DEFAULT_BINDING_NAME,
        runtime=RUNTIME_OLLAMA,
        model_name="llama3.1",
        parameters={},
        created_at=now,
    )
    PostgresModelBindingStore(engine).add_binding(orphaned)

    viste = _seeded_list(profiles, owner)
    assert len(viste) == 1
    assert {v.profile.kind for v in viste} == {AProfile.ASSISTANT}

    # Nessun binding duplicato: quello preesistente è stato riusato.
    binding_rows = _query(
        dbs.admin,
        "SELECT id, name FROM model_bindings",
    )
    assert len(binding_rows) == 1
    (binding_id_riga, binding_name) = binding_rows[0]
    assert binding_id_riga == orphaned.id
    assert binding_name == DEFAULT_BINDING_NAME
    # Tutte le versioni sono agganciate al binding orfano riusato.
    binding_refs = _query(dbs.admin, "SELECT DISTINCT model_binding_id FROM profile_versions")
    assert binding_refs == [(orphaned.id,)]


class _ConnConGuasto:
    """Connection di fault injection: fallisce alla N-esima istruzione."""

    def __init__(self, conn: Connection, falli_a: int) -> None:
        self._conn = conn
        self._falli_a = falli_a

    def execute(self, *args: object, **kwargs: object) -> object:
        self._n = getattr(self, "_n", 0) + 1
        if self._n == self._falli_a:
            raise RuntimeError("guasto iniettato nel passo atomico")
        return self._conn.execute(*args, **kwargs)

    def __getattr__(self, name: str) -> object:
        return getattr(self._conn, name)


class _TransazioneConGuasto:
    def __init__(self, interno: object, falli_a: int) -> None:
        self._interno = interno
        self._falli_a = falli_a

    def __enter__(self) -> _ConnConGuasto:
        return _ConnConGuasto(self._interno.__enter__(), self._falli_a)  # type: ignore[union-attr]

    def __exit__(self, *ecc: object) -> bool:
        return bool(self._interno.__exit__(*ecc))  # type: ignore[union-attr]


class _EngineConGuasto:
    """Engine che guasta alla N-esima istruzione della transazione (R03)."""

    def __init__(self, engine: object, falli_a: int) -> None:
        self._engine = engine
        self._falli_a = falli_a

    def begin(self) -> _TransazioneConGuasto:
        return _TransazioneConGuasto(self._engine.begin(), self._falli_a)  # type: ignore[union-attr]

    def dispose(self) -> None:
        self._engine.dispose()  # type: ignore[union-attr]


def test_guasto_a_ogni_step_lascia_zero_residui(setup) -> None:
    """R03: guasto iniettato a ogni fase del passo atomico su PostgreSQL:
    il rollback non lascia righe a nessuno step, e il seeding ripetuto
    completa il solo Assistente con un binding.

    Su scope vuoto la transazione esegue 7 istruzioni: 2 ``set_config``,
    insert + read-back del binding e del profilo, poi insert della versione.
    """
    dbs, engine, _, owner, _ = setup

    def servizio_guasto(falli_a: int) -> ProfileService:
        return ProfileService(
            PostgresProfileRepository(engine),
            PostgresModelBindingStore(engine),
            EmptyModelCatalog(),
            SystemClock(),
            PostgresProfileDefaultsSeeder(_EngineConGuasto(engine, falli_a)),
            default_model_name="llama3.1",
        )

    for falli_a in (3, 4, 5, 6, 7):
        with pytest.raises(RuntimeError, match="guasto iniettato"):
            servizio_guasto(falli_a).ensure_defaults(owner)
        for table in TABLES:
            assert _query(dbs.admin, f"SELECT count(*) FROM {table}") == [(0,)]

    # Con l'engine integro il seeding ripetuto completa tutto da zero.
    service = ProfileService(
        PostgresProfileRepository(engine),
        PostgresModelBindingStore(engine),
        EmptyModelCatalog(),
        SystemClock(),
        PostgresProfileDefaultsSeeder(engine),
        default_model_name="llama3.1",
    )
    viste = _seeded_list(service, owner)
    assert len(viste) == 1
    assert _query(dbs.admin, "SELECT count(*) FROM model_bindings") == [(1,)]
    assert _query(dbs.admin, "SELECT count(*) FROM profiles") == [(1,)]
    assert _query(dbs.admin, "SELECT count(*) FROM profile_versions") == [(1,)]


def test_lista_e_lettura_concordano_sulla_versione_corrente(setup) -> None:
    """B-03.2-07: ``list_profiles`` e ``get_profile`` selezionano la stessa
    versione, anche con più versioni per profilo.

    Difetto originale: ``get_profile`` restituiva ``rows[0]`` con
    ORDER BY crescente — cioè la versione più *vecchia* — mentre
    ``list_profiles`` deduplicava in Python scegliendo la massima.
    Il fix usa ``DISTINCT ON (p.id) ... ORDER BY p.id, v.created_at DESC,
    v.id DESC`` in entrambe le letture.
    """
    _, engine, profiles, owner, _ = setup
    view = _seeded_list(profiles, owner)[0]

    # Nuovo binding + nuova versione (created_at successivo).
    custom = ModelBinding(
        id=new_id(),
        organization_id=owner.organization_id,
        owner_id=owner.user_id,
        name="personalizzato-07",
        runtime=RUNTIME_OLLAMA,
        model_name="llama3.1",
        parameters={"temperature": 0.5, "sampling": {"stop": ["END"]}},
        created_at=_now(),
    )
    PostgresModelBindingStore(engine).add_binding(custom)
    nuova = ProfileVersion(
        id=new_id(),
        profile_id=view.profile.id,
        organization_id=owner.organization_id,
        owner_id=owner.user_id,
        version="1.1.0",
        model_binding_id=custom.id,
        instructions=view.version.instructions,
        created_at=_now(),
    )
    PostgresProfileRepository(engine).add_version(nuova)

    repo = PostgresProfileRepository(engine)
    scope = Scope(owner.organization_id, owner.user_id)
    dalla_lista = next(v for v in _seeded_list(profiles, owner) if v.profile.id == view.profile.id)
    (_, dalla_get) = repo.get_profile(scope, view.profile.id)  # type: ignore[misc]
    # La risoluzione esplicita (ADR 0003) richiede il modello presente
    # nel runtime: il catalogo di test contiene ``llama3.1``. Il
    # catalogo non incide sulla selezione della versione, che è l'oggetto
    # del test.
    risolutore = ProfileService(
        repo,
        PostgresModelBindingStore(engine),
        InMemoryModelCatalog(
            (
                ModelInfo(
                    name="llama3.1",
                    runtime=RUNTIME_OLLAMA,
                    digest="sha256:test-llama3.1",
                    status=ModelStatus.QUALIFIED,
                    capabilities=("chat",),
                ),
            )
        ),
        SystemClock(),
        PostgresProfileDefaultsSeeder(engine),
        default_model_name="llama3.1",
    )
    snapshot = asyncio.run(risolutore.resolve_binding(owner, view.profile.id))

    assert dalla_lista.version.version == "1.1.0"
    assert dalla_get.version == "1.1.0"
    assert snapshot.profile_version == "1.1.0"
    assert snapshot.profile_version_id == nuova.id
    assert snapshot.binding_id == custom.id
    assert snapshot.digest == "sha256:test-llama3.1"
    assert snapshot.parameters == {"temperature": 0.5, "sampling": {"stop": ("END",)}}
    assert dalla_lista.version.id == nuova.id
    assert dalla_get.id == nuova.id


def test_pareggio_timestamp_risolto_da_id_su_postgres(setup) -> None:
    """B-03.2-07: due versioni con lo stesso ``created_at`` restano ordinabili
    da ``v.id`` (tiebreaker deterministico dello stesso contratto Postgres)."""
    _, engine, profiles, owner, _ = setup
    view = _seeded_list(profiles, owner)[0]

    # Prima annulliamo il tempo: usiamo un solo istante per entrambe le
    # nuove versioni, così il tiebreaker cade sull'id.
    stesso_momento = _now()
    id_minore, id_maggiore = sorted([new_id(), new_id()])
    repo = PostgresProfileRepository(engine)
    for identificatore, version_label in ((id_minore, "1.0.a"), (id_maggiore, "1.0.b")):
        repo.add_version(
            ProfileVersion(
                id=identificatore,
                profile_id=view.profile.id,
                organization_id=owner.organization_id,
                owner_id=owner.user_id,
                version=version_label,
                model_binding_id=view.binding.id,
                instructions=view.version.instructions,
                created_at=stesso_momento,
            )
        )

    scope = Scope(owner.organization_id, owner.user_id)
    (_, corrente) = repo.get_profile(scope, view.profile.id)  # type: ignore[misc]
    dalla_lista = next(v for v in _seeded_list(profiles, owner) if v.profile.id == view.profile.id)
    # Fra le tre versioni (originale + due nuove al pareggio) vince quella
    # con id maggiore, quando i created_at sono uguali. La versione
    # originale ha created_at anteriore, quindi resta esclusa dalla gara.
    assert corrente.id == id_maggiore
    assert dalla_lista.version.id == id_maggiore


def test_switch_scritture_isolate_fra_due_principal(setup) -> None:
    """``profile_version_switches`` segue lo stesso isolamento delle altre
    tabelle del modulo (B-02.1): B non vede né conta le ricevute di A."""
    dbs, engine, profiles, owner, b = setup
    a_view = _seeded_list(profiles, owner)[0]
    writer = PostgresProfileVersionWriter(engine)
    binding = ModelBinding(
        id=new_id(),
        organization_id=owner.organization_id,
        owner_id=owner.user_id,
        name=str(new_id()),
        runtime=RUNTIME_OLLAMA,
        model_name="altro-modello",
        parameters={},
        created_at=_now(),
    )
    version = ProfileVersion(
        id=new_id(),
        profile_id=a_view.profile.id,
        organization_id=owner.organization_id,
        owner_id=owner.user_id,
        version="1.0.1",
        model_binding_id=binding.id,
        instructions="",
        created_at=_now(),
    )
    writer.switch_model(
        Scope(owner.organization_id, owner.user_id),
        a_view.profile.id,
        binding,
        version,
        expected_profile_version="1.0.0",
        idempotency_key="rls-check",
        request_hash="a" * 64,
    )

    scope_b = Scope(owner.organization_id, b.user_id)
    assert _as_principal(dbs.app, scope_b, "SELECT count(*) FROM profile_version_switches") == [
        (0,)
    ]
    scope_a = Scope(owner.organization_id, owner.user_id)
    assert _as_principal(dbs.app, scope_a, "SELECT count(*) FROM profile_version_switches") == [
        (1,)
    ]


def test_switch_stessa_chiave_concorrente_restituisce_una_sola_ricevuta(setup) -> None:
    """La seconda richiesta aspetta la prima e ripete il suo esito,
    anche se propone ID di binding/versione diversi."""
    dbs, engine, profiles, owner, _ = setup
    current = _seeded_list(profiles, owner)[0]
    scope = Scope(owner.organization_id, owner.user_id)
    writer = PostgresProfileVersionWriter(engine)
    barrier = threading.Barrier(2)

    def switch() -> tuple[ProfileVersion, ModelBinding]:
        binding = ModelBinding(
            id=new_id(),
            organization_id=scope.organization_id,
            owner_id=scope.user_id,
            name=str(new_id()),
            runtime=RUNTIME_OLLAMA,
            model_name="altro-modello",
            parameters={},
            created_at=_now(),
        )
        version = ProfileVersion(
            id=new_id(),
            profile_id=current.profile.id,
            organization_id=scope.organization_id,
            owner_id=scope.user_id,
            version="1.0.1",
            model_binding_id=binding.id,
            instructions="",
            created_at=_now(),
        )
        barrier.wait(timeout=10)
        return writer.switch_model(
            scope,
            current.profile.id,
            binding,
            version,
            expected_profile_version="1.0.0",
            idempotency_key="same-key-race",
            request_hash="d" * 64,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(switch)
        second = executor.submit(switch)
        first_result = first.result(timeout=15)
        second_result = second.result(timeout=15)

    assert first_result[0].id == second_result[0].id
    assert first_result[1].id == second_result[1].id
    assert _as_principal(
        dbs.app,
        scope,
        "SELECT count(*) FROM profile_version_switches WHERE idempotency_key = 'same-key-race'",
    ) == [(1,)]


def test_switch_proprietario_tabelle_non_elude_rls(setup) -> None:
    """FORCE ROW LEVEL SECURITY vale anche per ``profile_version_switches``."""
    dbs, engine, profiles, owner, _ = setup
    a_view = _seeded_list(profiles, owner)[0]
    writer = PostgresProfileVersionWriter(engine)
    binding = ModelBinding(
        id=new_id(),
        organization_id=owner.organization_id,
        owner_id=owner.user_id,
        name=str(new_id()),
        runtime=RUNTIME_OLLAMA,
        model_name="altro-modello",
        parameters={},
        created_at=_now(),
    )
    version = ProfileVersion(
        id=new_id(),
        profile_id=a_view.profile.id,
        organization_id=owner.organization_id,
        owner_id=owner.user_id,
        version="1.0.1",
        model_binding_id=binding.id,
        instructions="",
        created_at=_now(),
    )
    writer.switch_model(
        Scope(owner.organization_id, owner.user_id),
        a_view.profile.id,
        binding,
        version,
        expected_profile_version="1.0.0",
        idempotency_key="rls-owner-check",
        request_hash="b" * 64,
    )
    assert _query(dbs.migration, "SELECT count(*) FROM profile_version_switches") == [(0,)]


def test_switch_ricevuta_in_profilo_altrui_bloccata_dallo_schema(setup) -> None:
    """Il proprietario della ricevuta deve essere il proprietario del
    profilo referenziato: FK composito, stesso pattern delle versioni."""
    _, engine, profiles, owner, b = setup
    a_view = _seeded_list(profiles, owner)[0]
    invasion_id = new_id()
    with engine.begin() as conn:
        _set_scope_context(conn, Scope(owner.organization_id, b.user_id))
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO profile_version_switches "
                    "(id, profile_id, organization_id, owner_id, idempotency_key, "
                    " payload_hash, profile_version_id, created_at) "
                    "VALUES (:id, :pid, :org, :owner, :key, :hash, :vid, :created)"
                ),
                {
                    "id": invasion_id,
                    "pid": a_view.profile.id,
                    "org": owner.organization_id,
                    "owner": b.user_id,
                    "key": "invasione",
                    "hash": "c" * 64,
                    "vid": a_view.version.id,
                    "created": _now(),
                },
            )
