"""Catalogo dei modelli senza runtime di inference collegato.

Finché ``NEWRAY_OLLAMA_BASE_URL`` non è configurato il catalogo resta
vuoto: ``/models`` risponde vuoto, i profili mostrano ``model: null`` e
la risoluzione risponde ``MODEL_UNAVAILABLE`` recuperabile (ADR 0003:
nessun fallback invisibile). L'adapter Ollama (``adapters/ollama.py``)
fornisce lo stato reale quando il runtime è collegato.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from newray.kernel.errors import ModelUnavailable

from ..domain import ChatRequest, ModelInfo, ModelReadiness, ReadinessState, StreamEvent


class EmptyModelCatalog:
    """Catalogo vuoto: nessun modello disponibile nel runtime."""

    async def list_models(self) -> list[ModelInfo]:
        return []

    async def readiness(self, model_name: str | None) -> ModelReadiness:
        return ModelReadiness(ReadinessState.NOT_CONFIGURED, model_name)


class UnavailableChatModel:
    """Inference disabilitata: errore esplicito, mai risposta sintetica."""

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        raise ModelUnavailable("runtime di inference non configurato")
        yield  # pragma: no cover - mantiene il contratto AsyncIterator
