"""RLS conversazioni e messaggi su PostgreSQL reale (NewRay.md §§7.3, 22.2; B-01).

Verifica la seconda barriera con il ruolo applicativo reale
(``newray_app``, senza superuser/BYPASSRLS): due utenti della stessa
organizzazione, un'organizzazione esterna, il proprietario delle tabelle
(FORCE) e la barriera dello schema contro scritture cross-principal.
Niente fake: questi test identificano schema e ruoli reali.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from conftest import DatabaseHandles
from sqlalchemy import create_engine, text

from newray.infrastructure.database import create_engine as create_db_engine
from newray.kernel.clock import SystemClock
from newray.kernel.errors import Conflict, NotFound
from newray.kernel.identity import Role, Scope, new_id
from newray.modules.conversations import ConversationService, MessageRole
from newray.modules.conversations.adapters.postgres import (
    PostgresConversationRepository,
    PostgresMessageStore,
)
from newray.modules.identity import IdentityService
from newray.modules.identity.adapters.postgres import (
    PostgresOwnerBootstrap,
    PostgresSessionStore,
    PostgresUserRepository,
)
from newray.modules.identity.domain import Organization, User


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
    conversations = ConversationService(
        PostgresConversationRepository(engine), PostgresMessageStore(engine), SystemClock()
    )
    owner = identity.bootstrap_owner("A", "test-passphrase-1234")
    b = User(new_id(), owner.organization_id, "B", Role.MEMBER, _now())
    assert PostgresUserRepository(engine).add_user(b) is True
    identity.create_session(b)
    yield test_databases, engine, identity, conversations, owner, b
    engine.dispose()


def test_due_principal_stessa_organizzazione_sono_isolati(setup) -> None:
    dbs, _, _, conversations, owner, b = setup
    scope_a = Scope(owner.organization_id, owner.user_id)
    scope_b = Scope(owner.organization_id, b.id)

    conversation = conversations.create_conversation(owner, "Segreta")
    conversations.add_user_message(owner, conversation.id, "dime")
    conversations.add_user_message(owner, conversation.id, "due")

    # B non vede né la conversazione né i messaggi di A (NewRay.md §7.3).
    assert _as_principal(dbs.app, scope_b, "SELECT count(*) FROM conversations") == [(0,)]
    assert _as_principal(dbs.app, scope_b, "SELECT count(*) FROM messages") == [(0,)]
    # A vede i propri dati.
    assert _as_principal(dbs.app, scope_a, "SELECT count(*) FROM conversations") == [(1,)]
    assert _as_principal(dbs.app, scope_a, "SELECT count(*) FROM messages") == [(2,)]

    # I repository, pur con il ruolo applicativo, non attraversano lo scope.
    repo = PostgresConversationRepository(create_db_engine(dbs.app))
    assert repo.get_conversation(scope_b, conversation.id) is None
    assert repo.get_conversation(scope_a, conversation.id) is not None


def test_senza_contesto_nessun_dato(setup) -> None:
    dbs, _, _, conversations, owner, _ = setup
    conversations.create_conversation(owner, "Segreta")
    # GUC assenti → policy non soddisfatta: default deny (NewRay.md §7.3).
    assert _query(dbs.app, "SELECT count(*) FROM conversations") == [(0,)]
    assert _query(dbs.app, "SELECT count(*) FROM messages") == [(0,)]


def test_proprietario_tabelle_non_elude_rls(setup) -> None:
    """FORCE ROW LEVEL SECURITY: vale anche per il ruolo delle migrazioni."""
    dbs, _, _, conversations, owner, _ = setup
    conversations.create_conversation(owner, "Segreta")
    assert _query(dbs.migration, "SELECT count(*) FROM conversations") == [(0,)]
    assert _query(dbs.migration, "SELECT count(*) FROM messages") == [(0,)]


def test_organizzazione_esterna_non_legge(setup) -> None:
    dbs, engine, _, conversations, owner, _ = setup
    org2 = Organization(new_id(), "Altro", _now())
    repo = PostgresUserRepository(engine)
    c = User(new_id(), org2.id, "C", Role.MEMBER, _now())
    repo.add_organization(Scope(org2.id, c.id), org2)
    assert repo.add_user(c) is True

    conversation = conversations.create_conversation(owner, "Segreta")
    scope_c = Scope(org2.id, c.id)
    assert _as_principal(dbs.app, scope_c, "SELECT count(*) FROM conversations") == [(0,)]
    assert _as_principal(dbs.app, scope_c, "SELECT count(*) FROM messages") == [(0,)]
    assert PostgresConversationRepository(engine).get_conversation(scope_c, conversation.id) is None


def test_sequenza_stretta_persistita(setup) -> None:
    dbs, _, _, conversations, owner, _ = setup
    conversation = conversations.create_conversation(owner, "Lavoro")
    first = conversations.add_user_message(owner, conversation.id, "primo")
    second = conversations.add_user_message(owner, conversation.id, "secondo")
    assert (first.sequence, second.sequence) == (1, 2)

    scope_a = Scope(owner.organization_id, owner.user_id)
    rows = _as_principal(
        dbs.app,
        scope_a,
        "SELECT sequence FROM messages WHERE conversation_id = :cid ORDER BY sequence",
        {"cid": str(conversation.id)},
    )
    assert rows == [(1,), (2,)]


def test_append_concorrenti_hanno_sequenze_distinte_e_ordinamento_recente(setup) -> None:
    dbs, _, _, conversations, owner, _ = setup
    conversation = conversations.create_conversation(owner, "Lavoro")
    scope = owner.scope

    def append(content: str) -> int:
        engine = create_db_engine(dbs.app)
        try:
            return (
                PostgresMessageStore(engine)
                .append(scope, conversation.id, MessageRole.USER, content, _now())
                .sequence
            )
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as executor:
        sequences = sorted(executor.map(append, ("primo", "secondo")))

    assert sequences == [1, 2]
    scope_a = Scope(owner.organization_id, owner.user_id)
    assert _as_principal(
        dbs.app,
        scope_a,
        "SELECT next_sequence FROM conversations WHERE id = :id",
        {"id": str(conversation.id)},
    ) == [(3,)]
    assert _as_principal(
        dbs.app,
        scope_a,
        "SELECT updated_at > created_at FROM conversations WHERE id = :id",
        {"id": str(conversation.id)},
    ) == [(True,)]


def test_replay_idempotente_non_muta_conversazione(setup) -> None:
    dbs, engine, _, conversations, owner, _ = setup
    conversation = conversations.create_conversation(owner, "Lavoro")
    store = PostgresMessageStore(engine)
    scope = owner.scope
    first = store.append(
        scope,
        conversation.id,
        MessageRole.USER,
        "primo",
        _now(),
        idempotency_key="msg-1",
    )
    scope_a = Scope(owner.organization_id, owner.user_id)
    before = _as_principal(
        dbs.app,
        scope_a,
        "SELECT next_sequence, updated_at FROM conversations WHERE id = :id",
        {"id": str(conversation.id)},
    )
    replay = store.append(
        scope,
        conversation.id,
        MessageRole.USER,
        "primo",
        _now(),
        idempotency_key="msg-1",
    )
    after = _as_principal(
        dbs.app,
        scope_a,
        "SELECT next_sequence, updated_at FROM conversations WHERE id = :id",
        {"id": str(conversation.id)},
    )
    assert replay == first
    assert after == before
    with pytest.raises(Conflict):
        store.append(
            scope,
            conversation.id,
            MessageRole.USER,
            "diverso",
            _now(),
            idempotency_key="msg-1",
        )
    assert (
        _as_principal(
            dbs.app,
            scope_a,
            "SELECT next_sequence, updated_at FROM conversations WHERE id = :id",
            {"id": str(conversation.id)},
        )
        == before
    )


def test_versione_obsoleta_non_consente_due_mutazioni(setup) -> None:
    _, _, _, conversations, owner, _ = setup
    conversation = conversations.create_conversation(owner, "Prima")
    renamed = conversations.rename_conversation(
        owner,
        conversation.id,
        "Seconda",
        expected_updated_at=conversation.updated_at,
    )
    assert renamed.updated_at > conversation.updated_at
    with pytest.raises(Conflict):
        conversations.rename_conversation(
            owner,
            conversation.id,
            "Terza",
            expected_updated_at=conversation.updated_at,
        )
    with pytest.raises(Conflict):
        conversations.delete_conversation(
            owner,
            conversation.id,
            expected_updated_at=conversation.updated_at,
        )


def test_scrittura_in_conversazione_altrui_bloccata_dallo_schema(setup) -> None:
    """RLS non espone la conversazione altrui neppure alla mutazione.

    L'adapter prende ora un lock sulla riga padre prima dell'INSERT per
    serializzare il contatore (0004); sotto scope estraneo la policy la rende
    invisibile e il contratto pubblico restituisce quindi ``NotFound``.
    I vincoli compositi di 0002 restano la barriera dello schema per ogni
    INSERT che arrivi a quella fase.
    """
    _, engine, _, conversations, owner, b = setup
    conversation = conversations.create_conversation(owner, "Segreta")
    scope_b = Scope(owner.organization_id, b.id)
    store = PostgresMessageStore(engine)
    with pytest.raises(NotFound):
        store.append(scope_b, conversation.id, MessageRole.ASSISTANT, "invasione", _now())


def test_cascata_cancellazione(setup) -> None:
    dbs, _, _, conversations, owner, _ = setup
    conversation = conversations.create_conversation(owner, "Da cancellare")
    conversations.add_user_message(owner, conversation.id, "dime")
    conversations.delete_conversation(owner, conversation.id)

    scope_a = Scope(owner.organization_id, owner.user_id)
    assert _as_principal(dbs.app, scope_a, "SELECT count(*) FROM conversations") == [(0,)]
    assert _as_principal(dbs.app, scope_a, "SELECT count(*) FROM messages") == [(0,)]
