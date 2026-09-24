"""API pubblica del modulo conversations (NewRay.md §5.1).

Questo è l'unico punto di import per gli altri moduli e per il bootstrap:
mai importare ``adapters`` o tabelle private da qui.
"""

from newray.modules.conversations.application import DEFAULT_PAGE_SIZE, ConversationService
from newray.modules.conversations.domain import (
    MAX_MESSAGE_LENGTH,
    MAX_TITLE_LENGTH,
    Conversation,
    Message,
    MessageRole,
)
from newray.modules.conversations.ports import ConversationRepository, MessageStore

__all__ = [
    "Conversation",
    "ConversationRepository",
    "ConversationService",
    "DEFAULT_PAGE_SIZE",
    "MAX_MESSAGE_LENGTH",
    "MAX_TITLE_LENGTH",
    "Message",
    "MessageRole",
    "MessageStore",
]
