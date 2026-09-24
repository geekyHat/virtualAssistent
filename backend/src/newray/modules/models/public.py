"""API pubblica del modulo models (NewRay.md §5.1; B-03).

Questo è l'unico punto di import per gli altri moduli e per il
bootstrap: mai importare ``adapters`` o dettagli privati da qui.
"""

from newray.modules.models.domain import (
    DEFAULT_MODEL_NAME,
    MAX_CHAT_MESSAGE_LENGTH,
    MAX_MODEL_NAME_LENGTH,
    RUNTIME_OLLAMA,
    ChatMessage,
    ChatRequest,
    ChatRole,
    Completion,
    ContentDelta,
    ModelInfo,
    ModelReadiness,
    ModelStatus,
    ReadinessState,
    StreamEvent,
    ToolCallRequest,
    ToolSchema,
)
from newray.modules.models.ports import ChatModel, ModelCatalog

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
    "ToolCallRequest",
    "ToolSchema",
]
