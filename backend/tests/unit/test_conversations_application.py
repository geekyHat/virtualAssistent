"""Casi d'uso conversazioni e messaggi (NewRay.md §7.2; B-01).

Fake: verificano il contratto (scope obbligatorio, sequenza per
conversazione, paginazione a cursore, esiti). La qualità live e la RLS
reale sono provate in ``tests/integration`` su PostgreSQL (NewRay.md §22.2).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fakes import FakeClock, InMemoryConversationRepository, InMemoryMessageStore
from newray.kernel.errors import Conflict, DomainError, NotFound
from newray.kernel.identity import Principal, Role, new_id
from newray.modules.conversations import MAX_TITLE_LENGTH, ConversationService, MessageRole

START = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
ORG = new_id()


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


def _service(clock: FakeClock) -> ConversationService:
    repository = InMemoryConversationRepository()
    return ConversationService(repository, InMemoryMessageStore(repository), clock)


def test_crea_conversazione_nello_scope_del_principal(
    principal: Principal, clock: FakeClock
) -> None:
    service = _service(clock)
    conversation = service.create_conversation(principal, "Primo lavoro")
    assert conversation.owner_id == principal.user_id
    assert conversation.organization_id == principal.organization_id
    assert conversation.title == "Primo lavoro"
    assert conversation.created_at == clock.now()
    assert conversation.updated_at == conversation.created_at


def test_titolo_fuori_limite_è_rifiutato(principal: Principal, clock: FakeClock) -> None:
    service = _service(clock)
    with pytest.raises(ValueError):
        service.create_conversation(principal, "x" * (MAX_TITLE_LENGTH + 1))


def test_lista_ordina_dalle_più_recenti_e_pagina(principal: Principal, clock: FakeClock) -> None:
    service = _service(clock)
    prima = service.create_conversation(principal, "Prima")
    clock.advance(timedelta(minutes=1))
    seconda = service.create_conversation(principal, "Seconda")
    clock.advance(timedelta(minutes=1))
    terza = service.create_conversation(principal, "Terza")

    page, cursor = service.list_conversations(principal, None, 2)
    assert [c.id for c in page] == [terza.id, seconda.id]
    assert cursor is not None

    page2, cursor2 = service.list_conversations(principal, cursor, 2)
    assert [c.id for c in page2] == [prima.id]
    assert cursor2 is None


def test_lista_con_cursore_non_valido(principal: Principal, clock: FakeClock) -> None:
    service = _service(clock)
    with pytest.raises(DomainError):
        service.list_conversations(principal, "cursore-non-valido", 50)


def test_conversazione_altrui_non_è_rivelata(
    principal: Principal, other: Principal, clock: FakeClock
) -> None:
    service = _service(clock)
    conversation = service.create_conversation(principal, "Segreta")
    with pytest.raises(NotFound):
        service.get_conversation(other, conversation.id)
    items, cursor = service.list_conversations(other, None, 50)
    assert items == []
    assert cursor is None


def test_rinomina_aggiorna_titolo_e_ultimo_aggiornamento(
    principal: Principal, clock: FakeClock
) -> None:
    service = _service(clock)
    conversation = service.create_conversation(principal, "Vecchio")
    clock.advance(timedelta(minutes=5))
    renamed = service.rename_conversation(principal, conversation.id, "Nuovo")
    assert renamed.title == "Nuovo"
    assert renamed.updated_at == clock.now()
    assert renamed.created_at == conversation.created_at
    with pytest.raises(NotFound):
        service.rename_conversation(principal, new_id(), "X")


def test_cancella_e_la_seconda_volta_è_404(principal: Principal, clock: FakeClock) -> None:
    service = _service(clock)
    conversation = service.create_conversation(principal, "Da cancellare")
    service.delete_conversation(principal, conversation.id)
    with pytest.raises(NotFound):
        service.get_conversation(principal, conversation.id)
    with pytest.raises(NotFound):
        service.delete_conversation(principal, conversation.id)


def test_cancella_conversazione_altrui_è_404(
    principal: Principal, other: Principal, clock: FakeClock
) -> None:
    service = _service(clock)
    conversation = service.create_conversation(principal, "Segreta")
    with pytest.raises(NotFound):
        service.delete_conversation(other, conversation.id)


def test_messaggi_con_sequenza_stretta_per_conversazione(
    principal: Principal, clock: FakeClock
) -> None:
    service = _service(clock)
    a = service.create_conversation(principal, "A")
    b = service.create_conversation(principal, "B")
    sequence = [service.add_user_message(principal, a.id, f"m{i}").sequence for i in range(3)]
    assert sequence == [1, 2, 3]
    # La sequenza è per conversazione: ogni conversazione riparte da 1.
    assert service.add_user_message(principal, b.id, "altro").sequence == 1


def test_lista_messaggi_dopo_cursore(principal: Principal, clock: FakeClock) -> None:
    service = _service(clock)
    a = service.create_conversation(principal, "A")
    for i in range(1, 4):
        service.add_user_message(principal, a.id, f"m{i}")
    page = service.list_messages(principal, a.id, after_sequence=1, limit=10)
    assert [m.sequence for m in page] == [2, 3]
    assert all(m.role is MessageRole.USER for m in page)


def test_append_aggiorna_ordinamento_e_replay_idempotente_non_lo_muta(
    principal: Principal, clock: FakeClock
) -> None:
    service = _service(clock)
    first = service.create_conversation(principal, "Prima")
    clock.advance(timedelta(minutes=1))
    second = service.create_conversation(principal, "Seconda")

    appended = service.add_user_message(principal, first.id, "ciao", idempotency_key="m-1")
    after_append = service.get_conversation(principal, first.id)
    assert appended.sequence == 1
    assert after_append.updated_at > second.updated_at
    assert [item.id for item in service.list_conversations(principal)[0]] == [first.id, second.id]

    replayed = service.add_user_message(principal, first.id, "ciao", idempotency_key="m-1")
    assert replayed == appended
    assert service.get_conversation(principal, first.id).updated_at == after_append.updated_at
    with pytest.raises(Conflict):
        service.add_user_message(principal, first.id, "diverso", idempotency_key="m-1")


def test_rinomina_con_versione_obsoleta_non_sovrascrive(
    principal: Principal, clock: FakeClock
) -> None:
    service = _service(clock)
    conversation = service.create_conversation(principal, "Prima")
    changed = service.rename_conversation(principal, conversation.id, "Seconda")
    assert changed.updated_at > conversation.updated_at
    with pytest.raises(Conflict):
        service.rename_conversation(
            principal,
            conversation.id,
            "Persa",
            expected_updated_at=conversation.updated_at,
        )


def test_messaggi_in_conversazione_altrui_sono_404(
    principal: Principal, other: Principal, clock: FakeClock
) -> None:
    service = _service(clock)
    conversation = service.create_conversation(principal, "Segreta")
    with pytest.raises(NotFound):
        service.add_user_message(other, conversation.id, "invasione")
    with pytest.raises(NotFound):
        service.list_messages(other, conversation.id)


def test_messaggio_vuoto_è_rifiutato(principal: Principal, clock: FakeClock) -> None:
    service = _service(clock)
    conversation = service.create_conversation(principal, "A")
    with pytest.raises(ValueError):
        service.add_user_message(principal, conversation.id, "")


def test_lista_messaggi_conversazione_assente_è_404(principal: Principal, clock: FakeClock) -> None:
    service = _service(clock)
    with pytest.raises(NotFound):
        service.list_messages(principal, new_id())
