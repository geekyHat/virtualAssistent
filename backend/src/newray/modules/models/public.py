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
)
from newray.modules.models.hardware import discover_hardware
from newray.modules.models.ports import ChatModel, ModelCatalog
from newray.modules.models.qualification import (
    HardwareFingerprint,
    QualificationRecord,
    QualificationStore,
    qualified_capabilities_for,
)
from newray.modules.models.qualification_campaign import (
    CampaignConfig,
    CampaignResult,
    ProbeOutcome,
    RuntimeProbe,
    run_campaign,
)

__all__ = [
    "CampaignConfig",
    "CampaignResult",
    "ChatMessage",
    "ChatModel",
    "ChatRequest",
    "ChatRole",
    "Completion",
    "ContentDelta",
    "DEFAULT_MODEL_NAME",
    "HardwareFingerprint",
    "MAX_CHAT_MESSAGE_LENGTH",
    "MAX_MODEL_NAME_LENGTH",
    "ModelCatalog",
    "ModelInfo",
    "ModelReadiness",
    "ModelStatus",
    "ProbeOutcome",
    "QualificationRecord",
    "QualificationStore",
    "ReadinessState",
    "RuntimeProbe",
    "RUNTIME_OLLAMA",
    "StreamEvent",
    "discover_hardware",
    "qualified_capabilities_for",
    "run_campaign",
]
