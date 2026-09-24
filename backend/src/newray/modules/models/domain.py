"""Dominio dei modelli e della richiesta di chat (NewRay.md §§6.1, 8.2, 9.1; B-03).

Il modulo models possiede cosa significa avere un modello su un runtime:
il catalogo (``ModelInfo``, ``ModelStatus``) e il contratto di inference
(``ChatRequest`` ed eventi di stream). I profili (B-02) riferiscono questi
tipi nei binding; i run (B-04) consumeranno ``ChatModel`` con lo snapshot
della risoluzione del binding (ADR 0003).

Gli errori vendor non compaiono qui: li mappa l'adapter sui codici stabili
di dominio (NewRay.md §6.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum

#: Runtime di inference supportato in questa fase (B-03: adapter Ollama).
RUNTIME_OLLAMA = "ollama"

#: Valore legacy mantenuto per compatibilità dei contratti/fixture B-02.
#: Il bootstrap operativo usa NEWRAY_DEFAULT_MODEL_NAME, mai questa costante.
DEFAULT_MODEL_NAME = "llama3.1"

#: Limiti di lunghezza: fonte unica per dominio, DTO e schema
#: (NewRay.md §19.2).
MAX_MODEL_NAME_LENGTH = 200
MAX_CHAT_MESSAGE_LENGTH = 50_000


class ModelStatus(StrEnum):
    """Stati distinti del catalogo (NewRay.md §9.1): essere installati,
    compatibili e qualificati sono fatti diversi."""

    INSTALLED = "installed"
    COMPATIBLE = "compatible"
    QUALIFIED = "qualified"


class ReadinessState(StrEnum):
    """Diagnostica del candidato, distinta dalla qualifica delle capacità."""

    NOT_CONFIGURED = "not_configured"
    UNREACHABLE = "unreachable"
    RUNTIME_ERROR = "runtime_error"
    CATALOG_EMPTY = "catalog_empty"
    MODEL_MISSING = "model_missing"
    ARTIFACT_INCOMPATIBLE = "artifact_incompatible"
    INSTALLED_UNVERIFIED = "installed_unverified"


@dataclass(frozen=True, slots=True)
class ModelReadiness:
    """Solo fatti osservati: /api/show dichiara, non qualifica, capacità."""

    state: ReadinessState
    model_name: str | None
    digest: str | None = None
    declared_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """Modello di un runtime, dal catalogo (NewRay.md §9.1).

    I tag mobili servono alla discovery, non alla riproducibilità: lo
    snapshot usa il digest, non il tag.
    """

    name: str
    runtime: str
    digest: str | None
    status: ModelStatus
    capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.name) <= MAX_MODEL_NAME_LENGTH:
            raise ValueError(
                f"ModelInfo: nome del modello fuori dai limiti (1-{MAX_MODEL_NAME_LENGTH})"
            )
        if self.runtime != RUNTIME_OLLAMA:
            raise ValueError(f"ModelInfo: runtime non supportato ({self.runtime!r})")


class ChatRole(StrEnum):
    """Ruoli dei messaggi nel contesto di chat (NewRay.md §8.2)."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """Messaggio del contesto di chat: provenienza esplicita via ruolo."""

    role: ChatRole
    content: str

    def __post_init__(self) -> None:
        if not 1 <= len(self.content) <= MAX_CHAT_MESSAGE_LENGTH:
            raise ValueError(
                f"ChatMessage: contenuto fuori dai limiti (1-{MAX_CHAT_MESSAGE_LENGTH})"
            )


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Richiesta di chat esplicita (ADR 0003): il modello è assegnato,
    mai inferito; nessun routing dietro le quinte.

    I limiti fanno parte della richiesta (``max_tokens`` e timeout di
    inattività): l'adapter applica il secondo tra due chunk del runtime.
    Non è una deadline complessiva: il worker del run (B-04/B-05) applicherà
    quel budget all'intera generazione, anche se il runtime continua a
    produrre piccoli chunk. Il recupero resta al worker, non a retry ciechi
    dell'adapter (§8.3).
    """

    model: str
    runtime: str
    messages: tuple[ChatMessage, ...]
    parameters: dict[str, object] = field(default_factory=dict)
    max_tokens: int | None = None
    inactivity_timeout: timedelta | None = None

    def __post_init__(self) -> None:
        if not 1 <= len(self.model) <= MAX_MODEL_NAME_LENGTH:
            raise ValueError(f"ChatRequest: modello fuori dai limiti (1-{MAX_MODEL_NAME_LENGTH})")
        if self.runtime != RUNTIME_OLLAMA:
            raise ValueError(f"ChatRequest: runtime non supportato ({self.runtime!r})")
        if not self.messages:
            raise ValueError("ChatRequest: almeno un messaggio è necessario")
        if self.max_tokens is not None and self.max_tokens < 1:
            raise ValueError("ChatRequest: max_tokens deve essere almeno 1")
        if self.inactivity_timeout is not None and self.inactivity_timeout <= timedelta(0):
            raise ValueError("ChatRequest: inactivity_timeout deve essere positivo")


@dataclass(frozen=True, slots=True)
class ContentDelta:
    """Frammento di contenuto dell'assistente prodotto dal modello."""

    text: str

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("ContentDelta: il testo non può essere vuoto")


@dataclass(frozen=True, slots=True)
class Completion:
    """Fine dello stream con la contabilizzazione (NewRay.md §8.1: le
    chiamate vanno contabilizzate). I conteggi sono ``None`` quando il
    runtime non li fornisce.

    ``eval_duration_ns`` è la durata di sola generazione in nanosecondi,
    restituita dal runtime (Ollama ``eval_duration``). ``None`` quando il
    runtime non la fornisce. Il throughput ``tokens_per_second`` è
    calcolato esclusivamente da ``completion_tokens / eval_duration_s``
    quando entrambi sono validi e positivi.
    """

    finish_reason: str
    prompt_tokens: int | None
    completion_tokens: int | None
    eval_duration_ns: int | None = None

    def __post_init__(self) -> None:
        if not self.finish_reason:
            raise ValueError("Completion: il motivo di fine non può essere vuoto")
        for value in (self.prompt_tokens, self.completion_tokens):
            if value is not None and value < 0:
                raise ValueError("Completion: i conteggi token non possono essere negativi")
        if self.eval_duration_ns is not None and self.eval_duration_ns < 0:
            raise ValueError("Completion: eval_duration_ns non può essere negativo")

    @property
    def tokens_per_second(self) -> float | None:
        if (
            self.completion_tokens is None
            or self.eval_duration_ns is None
            or self.completion_tokens <= 0
            or self.eval_duration_ns <= 0
        ):
            return None
        return round(self.completion_tokens / (self.eval_duration_ns / 1_000_000_000), 2)


#: Evento prodotto dallo stream di un ``ChatModel`` (NewRay.md §6.1).
StreamEvent = ContentDelta | Completion
