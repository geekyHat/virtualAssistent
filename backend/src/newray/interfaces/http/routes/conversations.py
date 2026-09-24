"""Route conversazioni e messaggi (NewRay.md §19.2; B-01).

Il principal è sempre risolto dal server (cookie opaco, HttpOnly); ogni
conversazione e messaggio è accessibile solo nel proprio scope (§7.3):
fuori scope risponde 404 uniforme, senza rivelarne l'esistenza.

I messaggi dell'utente entrano da qui; il ruolo è deciso dal server.
I messaggi dell'assistente entrano dal worker dei run (B-04/B-05) sulla
stessa porta di persistenza, non da questa superficie.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from newray.interfaces.http.dto.conversations import (
    ConversationDTO,
    ConversationListDTO,
    CreateConversationRequest,
    CreateMessageRequest,
    MessageDTO,
    MessageListDTO,
    RenameConversationRequest,
)
from newray.interfaces.http.middleware.identity import require_principal
from newray.interfaces.http.routes import API_PREFIX
from newray.kernel.identity import Principal
from newray.modules.conversations import (
    DEFAULT_PAGE_SIZE,
    Conversation,
    ConversationService,
    Message,
)


def _conversation(conversation: Conversation) -> ConversationDTO:
    return ConversationDTO(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _message(message: Message) -> MessageDTO:
    return MessageDTO(
        id=message.id,
        role=message.role,
        content=message.content,
        sequence=message.sequence,
        created_at=message.created_at,
    )


def build_router() -> APIRouter:
    """Costruisce le route conversazioni; il servizio viene da ``app.state``."""
    router = APIRouter(prefix=API_PREFIX)

    @router.get("/conversations", response_model=ConversationListDTO)
    def list_conversations(
        request: Request,
        principal: Principal = Depends(require_principal),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=200),
    ) -> ConversationListDTO:
        """Conversazioni proprie, dalle più recenti, con paginazione a cursore."""
        service: ConversationService = request.app.state.conversation_service
        items, next_cursor = service.list_conversations(principal, cursor, limit)
        return ConversationListDTO(
            items=[_conversation(item) for item in items], next_cursor=next_cursor
        )

    @router.post("/conversations", status_code=201, response_model=ConversationDTO)
    def create_conversation(
        request: Request,
        body: CreateConversationRequest,
        principal: Principal = Depends(require_principal),
    ) -> ConversationDTO:
        """Nuova conversazione posseduta dal principal (§7.2)."""
        service: ConversationService = request.app.state.conversation_service
        conversation = service.create_conversation(principal, body.title)
        return _conversation(conversation)

    @router.get("/conversations/{conversation_id}", response_model=ConversationDTO)
    def get_conversation(
        conversation_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> ConversationDTO:
        """Conversazione per ID; fuori scope → 404 (§7.3)."""
        service: ConversationService = request.app.state.conversation_service
        return _conversation(service.get_conversation(principal, conversation_id))

    @router.patch("/conversations/{conversation_id}", response_model=ConversationDTO)
    def rename_conversation(
        conversation_id: uuid.UUID,
        request: Request,
        body: RenameConversationRequest,
        principal: Principal = Depends(require_principal),
    ) -> ConversationDTO:
        """Cambia il titolo: aggiorna ``updated_at`` (ordine della lista).

        Con ``expected_updated_at`` nel body, versione attesa: stato
        cambiato dall'ultima lettura del client → 409."""
        service: ConversationService = request.app.state.conversation_service
        renamed = service.rename_conversation(
            principal,
            conversation_id,
            body.title,
            expected_updated_at=body.expected_updated_at,
        )
        return _conversation(renamed)

    @router.delete("/conversations/{conversation_id}", status_code=204)
    def delete_conversation(
        conversation_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(require_principal),
        expected_updated_at: Annotated[datetime | None, Query()] = None,
    ) -> None:
        """Cancellazione fisica con i messaggi (cascata, migration 0002).

        Con ``expected_updated_at`` (query), versione attesa: stato cambiato
        dall'ultima lettura del client → 409."""
        service: ConversationService = request.app.state.conversation_service
        service.delete_conversation(
            principal, conversation_id, expected_updated_at=expected_updated_at
        )

    @router.get("/conversations/{conversation_id}/messages", response_model=MessageListDTO)
    def list_messages(
        conversation_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(require_principal),
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=200),
    ) -> MessageListDTO:
        """Messaggi in ordine di sequenza, da ``after_sequence`` escluso."""
        service: ConversationService = request.app.state.conversation_service
        items = service.list_messages(
            principal, conversation_id, after_sequence=after_sequence, limit=limit
        )
        return MessageListDTO(
            items=[_message(item) for item in items],
            # ``after_sequence`` è esclusivo: il cursore restituito è l'ultimo
            # elemento effettivamente letto e può essere riutilizzato tal quale.
            next_sequence=items[-1].sequence if len(items) == limit else None,
        )

    @router.post(
        "/conversations/{conversation_id}/messages", status_code=201, response_model=MessageDTO
    )
    def create_message(
        conversation_id: uuid.UUID,
        request: Request,
        body: CreateMessageRequest,
        principal: Principal = Depends(require_principal),
    ) -> MessageDTO:
        """Messaggio dell'utente: ruolo deciso dal server, sequenza server-side.

        Con ``idempotency_key``, il retry della stessa richiesta non
        duplica il messaggio; stessa chiave con contenuto diverso → 409."""
        service: ConversationService = request.app.state.conversation_service
        message = service.add_user_message(
            principal,
            conversation_id,
            body.content,
            idempotency_key=body.idempotency_key,
        )
        return _message(message)

    return router
