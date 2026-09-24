"""Modulo conversazioni: i dati di lavoro della conversazione (NewRay.md §7.2).

L'API pubblica è ``public.py``; qui si riesportano solo i nomi pubblici,
così ``from newray.modules.conversations import ...`` resta l'unico punto
di import per i consumatori (mai ``adapters`` né tabelle private altrui).
"""

from newray.modules.conversations.public import (
    DEFAULT_PAGE_SIZE,
    MAX_MESSAGE_LENGTH,
    MAX_TITLE_LENGTH,
    Conversation,
    ConversationRepository,
    ConversationService,
    Message,
    MessageRole,
    MessageStore,
)

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
