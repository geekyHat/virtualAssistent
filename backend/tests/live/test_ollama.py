"""Prova live dell'adapter Ollama: opt-in, con modello e digest dichiarati (B-03).

Si esegue solo con tutte e tre le variabili definite:

- ``NEWRAY_LIVE_OLLAMA_BASE_URL``: base URL del runtime locale;
- ``NEWRAY_LIVE_OLLAMA_MODEL``: nome del modello installato;
- ``NEWRAY_LIVE_OLLAMA_DIGEST``: digest del modello: la riproducibilità
  passa dal digest, non dal tag (NewRay.md §9.1), quindi la prova
  dichiara l'esatto artefatto su cui corre.

Identifica l'ambiente (runtime + modello + macchina) e il contratto di
streaming: non garantisce la qualità generativa, che resta un fatto
esterno alla suite (NewRay.md §22.2). Senza configurazione si salta:
la suite ordinaria resta offline.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from newray.infrastructure.network import HttpClient
from newray.modules.models import (
    RUNTIME_OLLAMA,
    ChatMessage,
    ChatRequest,
    ChatRole,
    Completion,
    ContentDelta,
    ReadinessState,
)
from newray.modules.models.adapters.ollama import OllamaChatModel, OllamaModelCatalog

LIVE = pytest.mark.skipif(
    not (
        os.environ.get("NEWRAY_LIVE_OLLAMA_BASE_URL")
        and os.environ.get("NEWRAY_LIVE_OLLAMA_MODEL")
        and os.environ.get("NEWRAY_LIVE_OLLAMA_DIGEST")
    ),
    reason="NEWRAY_LIVE_OLLAMA_BASE_URL / NEWRAY_LIVE_OLLAMA_MODEL / "
    "NEWRAY_LIVE_OLLAMA_DIGEST non definiti (prova opt-in)",
)


def _live() -> tuple[str, str, str]:
    base_url = os.environ["NEWRAY_LIVE_OLLAMA_BASE_URL"]
    model = os.environ["NEWRAY_LIVE_OLLAMA_MODEL"]
    digest = os.environ["NEWRAY_LIVE_OLLAMA_DIGEST"]
    return base_url, model, digest


def _catalog_client(base_url: str) -> HttpClient:
    return HttpClient(base_url)


@LIVE
def test_catalogo_contiene_il_modello_dichiarato() -> None:
    base_url, model, digest = _live()

    async def scenario() -> None:
        client = _catalog_client(base_url)
        try:
            catalog = OllamaModelCatalog(client)
            models = await catalog.list_models()
        finally:
            await client.aclose()

        assert any(m.name == model and m.digest == digest for m in models), (
            f"modello {model!r} (digest {digest!r}) non presente nel catalogo"
        )

    asyncio.run(scenario())


@LIVE
def test_readiness_dichiara_senza_qualificare() -> None:
    base_url, model, digest = _live()

    async def scenario():
        client = _catalog_client(base_url)
        try:
            return await OllamaModelCatalog(client).readiness(model)
        finally:
            await client.aclose()

    observed = asyncio.run(scenario())
    assert observed.state is ReadinessState.INSTALLED_UNVERIFIED
    assert observed.digest == digest
    assert "completion" in observed.declared_capabilities


@LIVE
def test_stream_genera_un_completamento() -> None:
    base_url, model, _digest = _live()
    request = ChatRequest(
        model=model,
        runtime=RUNTIME_OLLAMA,
        messages=(ChatMessage(role=ChatRole.USER, content="Rispondi solo: ok"),),
        max_tokens=32,
    )

    async def scenario() -> list[Any]:
        client = _catalog_client(base_url)
        try:
            chat = OllamaChatModel(client)
            return [event async for event in chat.stream(request)]
        finally:
            await client.aclose()

    events = asyncio.run(scenario())

    deltas = [event for event in events if isinstance(event, ContentDelta)]
    assert deltas, "nessun contenuto generato"
    completion = events[-1]
    assert isinstance(completion, Completion)
    assert completion.finish_reason
    assert completion.completion_tokens is not None
    assert completion.completion_tokens > 0
    assert completion.eval_duration_ns is not None
    assert completion.eval_duration_ns > 0
    assert completion.tokens_per_second is not None
    assert completion.tokens_per_second > 0
    assert completion.tokens_per_second == round(
        completion.completion_tokens / (completion.eval_duration_ns / 1_000_000_000),
        2,
    )
