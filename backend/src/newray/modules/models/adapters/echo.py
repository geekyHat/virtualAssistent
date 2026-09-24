"""Adapter deterministico per test E2E e contratti (B-09.2).

Restituisce una risposta fissa senza rete: il percorso completo
(risoluzione binding → contesto → streaming → persistenza) è
verificabile senza GPU, Ollama o latenza. La risposta è prevedibile:
il test sa cosa aspettarsi.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..domain import ChatRequest, Completion, ContentDelta, StreamEvent


class EchoChatModel:
    """Modello deterministico: ripete il contenuto dell'ultimo messaggio user."""

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        last_user = ""
        for msg in request.messages:
            if msg.role.value == "user":
                last_user = msg.content
        reply = f"Echo: {last_user}" if last_user else "Echo: (nessun messaggio)"
        words = reply.split(" ")
        for i, word in enumerate(words):
            text = word if i == 0 else f" {word}"
            yield ContentDelta(text=text)
        yield Completion(
            finish_reason="stop",
            prompt_tokens=len(last_user),
            completion_tokens=len(reply),
            eval_duration_ns=None,
        )
