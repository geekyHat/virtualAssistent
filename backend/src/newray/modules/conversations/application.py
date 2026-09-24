"""Casi d'uso di conversazioni e messaggi (NewRay.md §§7.2, 19.2; B-01).

Il principal è sempre risolto dal server: nessun dato del client partecipa.
Conversazioni e messaggi sono i dati di lavoro della conversazione (§7.2):
proprietà esplicita, sequenza per conversazione, paginazione a cursore.
In B-01 i messaggi dell'utente entrano via API; quelli dell'assistente
entrano dal worker dei run (B-04/B-05) sulla stessa porta.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime

from newray.kernel.clock import Clock
from newray.kernel.errors import DomainError, NotFound
from newray.kernel.identity import Principal, new_id

from .domain import Conversation, Message, MessageRole
from .ports import ConversationRepository, MessageStore

#: Dimensione di pagina predefinita della paginazione a cursore (§19.2).
DEFAULT_PAGE_SIZE = 50


def _encode_cursor(updated_at: datetime, conversation_id: uuid.UUID) -> str:
    raw = f"{updated_at.isoformat()}|{conversation_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        timestamp, _, conversation_id = raw.partition("|")
        return datetime.fromisoformat(timestamp), uuid.UUID(conversation_id)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DomainError("cursore di paginazione non valido") from exc


class ConversationService:
    """Casi d'uso: ciclo di vita di conversazioni e messaggi in scope."""

    def __init__(
        self,
        conversations: ConversationRepository,
        messages: MessageStore,
        clock: Clock,
    ) -> None:
        self._conversations = conversations
        self._messages = messages
        self._clock = clock

    def create_conversation(self, principal: Principal, title: str) -> Conversation:
        """Nuova conversazione posseduta dal principal (§7.2)."""
        now = self._clock.now()
        conversation = Conversation(
            id=new_id(),
            organization_id=principal.organization_id,
            owner_id=principal.user_id,
            title=title,
            created_at=now,
            updated_at=now,
        )
        self._conversations.add_conversation(conversation)
        return conversation

    def list_conversations(
        self, principal: Principal, cursor: str | None = None, limit: int = DEFAULT_PAGE_SIZE
    ) -> tuple[list[Conversation], str | None]:
        """Pagina di conversazioni proprie, dalle più recenti.

        Restituisce anche il cursore della pagina successiva (``None``
        se la pagina è l'ultima): il client riprende da lì, senza
        duplicati né salti (§19.2).
        """
        before = _decode_cursor(cursor) if cursor else None
        items = self._conversations.list_conversations(principal.scope, before=before, limit=limit)
        next_cursor = (
            _encode_cursor(items[-1].updated_at, items[-1].id) if len(items) == limit else None
        )
        return items, next_cursor

    def get_conversation(self, principal: Principal, conversation_id: uuid.UUID) -> Conversation:
        """Conversazione per ID nel proprio scope; fuori scope → ``NotFound``
        (l'esistenza di risorse altrui non è rivelata)."""
        conversation = self._conversations.get_conversation(principal.scope, conversation_id)
        if conversation is None:
            raise NotFound("conversazione non trovata")
        return conversation

    def rename_conversation(
        self,
        principal: Principal,
        conversation_id: uuid.UUID,
        title: str,
        *,
        expected_updated_at: datetime | None = None,
    ) -> Conversation:
        """Rinomina con versione attesa opzionale: stato cambiato
        dall'ultima lettura → ``Conflict``, mai una sovrascrittura silenziosa."""
        renamed = self._conversations.rename_conversation(
            principal.scope,
            conversation_id,
            title,
            self._clock.now(),
            expected_updated_at=expected_updated_at,
        )
        if renamed is None:
            raise NotFound("conversazione non trovata")
        return renamed

    def delete_conversation(
        self,
        principal: Principal,
        conversation_id: uuid.UUID,
        *,
        expected_updated_at: datetime | None = None,
    ) -> None:
        """Cancellazione fisica con i messaggi (cascata, migration 0002),
        con versione attesa opzionale (``Conflict`` se obsoleta)."""
        if not self._conversations.delete_conversation(
            principal.scope, conversation_id, expected_updated_at=expected_updated_at
        ):
            raise NotFound("conversazione non trovata")

    def list_messages(
        self,
        principal: Principal,
        conversation_id: uuid.UUID,
        *,
        after_sequence: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> list[Message]:
        """Messaggi della conversazione, in ordine di sequenza."""
        self.get_conversation(principal, conversation_id)
        return self._messages.list(
            principal.scope, conversation_id, after_sequence=after_sequence, limit=limit
        )

    def list_recent_messages(
        self,
        principal: Principal,
        conversation_id: uuid.UUID,
        *,
        limit: int,
    ) -> list[Message]:
        """Ultimi messaggi per il contesto, mantenuti in ordine cronologico."""
        self.get_conversation(principal, conversation_id)
        return self._messages.list_recent(principal.scope, conversation_id, limit=limit)

    def find_exchange(
        self,
        principal: Principal,
        conversation_id: uuid.UUID,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Message, Message] | None:
        """Ricevuta scoped di una preview già conclusa."""
        self.get_conversation(principal, conversation_id)
        return self._messages.find_exchange(
            principal.scope,
            conversation_id,
            idempotency_key,
            request_hash,
        )

    def add_exchange(
        self,
        principal: Principal,
        conversation_id: uuid.UUID,
        user_content: str,
        assistant_content: str,
        *,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> tuple[Message, Message]:
        """Salva prompt e risposta in un'unica transazione corta."""
        self.get_conversation(principal, conversation_id)
        return self._messages.append_exchange(
            principal.scope,
            conversation_id,
            user_content,
            assistant_content,
            self._clock.now(),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )

    def add_user_message(
        self,
        principal: Principal,
        conversation_id: uuid.UUID,
        content: str,
        *,
        idempotency_key: str | None = None,
    ) -> Message:
        """Messaggio dell'utente: il ruolo è deciso dal server, mai dal payload.

        Con ``idempotency_key`` il retry della stessa richiesta non duplica
        il messaggio; stessa chiave con contenuto diverso → ``Conflict``.
        """
        self.get_conversation(principal, conversation_id)
        return self._messages.append(
            principal.scope,
            conversation_id,
            MessageRole.USER,
            content,
            self._clock.now(),
            idempotency_key=idempotency_key,
        )

    def add_assistant_message(
        self,
        principal: Principal,
        conversation_id: uuid.UUID,
        content: str,
    ) -> Message:
        """Messaggio dell'assistente: generato dal modello, persistito dal server."""
        self.get_conversation(principal, conversation_id)
        return self._messages.append(
            principal.scope,
            conversation_id,
            MessageRole.ASSISTANT,
            content,
            self._clock.now(),
        )
