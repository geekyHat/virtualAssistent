"""Modulo models: catalogo dei modelli e inference (NewRay.md §§6.1, 9.1; B-03).

L'API pubblica è ``public.py``; qui si riesportano solo i nomi pubblici,
così ``from newray.modules.models import ...`` resta l'unico punto di
import per i consumatori (mai ``adapters`` né dettagli privati altrui).
"""

from newray.modules.models.public import (
    DEFAULT_MODEL_NAME,
    MAX_CHAT_MESSAGE_LENGTH,
    MAX_MODEL_NAME_LENGTH,
    RUNTIME_OLLAMA,
    ChatMessage,
    ChatModel,
    ChatRequest,
    ChatRole,
    Completion,
    ContentDelta,
    ModelCatalog,
    ModelInfo,
    ModelReadiness,
    ModelStatus,
    ReadinessState,
    StreamEvent,
)

__all__ = [
    "ChatMessage",
    "ChatModel",
    "ChatRequest",
    "ChatRole",
    "Completion",
    "ContentDelta",
    "DEFAULT_MODEL_NAME",
    "MAX_CHAT_MESSAGE_LENGTH",
    "MAX_MODEL_NAME_LENGTH",
    "ModelCatalog",
    "ModelInfo",
    "ModelReadiness",
    "ModelStatus",
    "ReadinessState",
    "RUNTIME_OLLAMA",
    "StreamEvent",
]
