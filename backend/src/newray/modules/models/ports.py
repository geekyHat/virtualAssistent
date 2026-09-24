"""Porte del modulo models (NewRay.md §§5.1, 6.1; B-03).

``ModelCatalog`` è lo stato del runtime di inference: non è un dato
privato dell'utente, quindi nessun scope (NewRay.md §9.1). È asincrona
perché il runtime è un servizio di rete locale: i call site (profili,
worker dei run) sono già in contesto asincrono.

``ChatModel`` è il contratto di inference (NewRay.md §6.1): streaming
di contenuti, limiti espliciti, cancellazione terminando lo stream ed
errori tipizzati — i guasti vendor sono mappati sui codici stabili di
dominio, mai esposti al client.

Gli adapter implementano le porte; nei test i fake (``tests/fakes``) e la
suite di contratto verificano il contratto senza rete (NewRay.md §22.1).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from .domain import ChatRequest, ModelInfo, ModelReadiness, StreamEvent


class ModelCatalog(Protocol):
    """Catalogo dei modelli del runtime di inference (NewRay.md §9.1).

    L'adapter Ollama (B-03) fornisce lo stato reale; finché nessun
    runtime è collegato il catalogo è vuoto e la risoluzione risponde
    ``MODEL_UNAVAILABLE`` recuperabile (ADR 0003).
    """

    async def list_models(self) -> list[ModelInfo]:
        """Modelli noti al runtime, con digest e stato (installed/
        compatible/qualified sono distinti, §9.1)."""
        ...

    async def readiness(self, model_name: str | None) -> ModelReadiness:
        """Stato osservato del candidato, senza caricare o qualificare pesi."""
        ...


class ChatModel(Protocol):
    """Modello generativo assegnato: streaming esplicito, nessun routing
    invisibile (ADR 0003, NewRay.md §6.1).

    Cancellazione: il consumer deve chiudere esplicitamente lo stream con
    ``aclose`` (un semplice ``break`` non è una garanzia); l'adapter rilascia
    la connessione. Limiti: ``max_tokens`` e ``inactivity_timeout`` sono
    applicati al runtime. La deadline complessiva è responsabilità del run
    B-04/B-05, così copre anche stream che continuano a produrre chunk.
    Errori: tipi di dominio con codici stabili.
    """

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]: ...
