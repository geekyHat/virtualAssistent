"""Executor durevole: bridge ``ChatModel`` → ``RunOutcome`` (P-05).

Legge lo snapshot congelato del run, ricostruisce la ``ChatRequest`` e
consuma lo stream del binding senza reintrodurre routing o fallback
invisibili. Applica una **deadline complessiva** distinta dal timeout di
inattività dell'adapter: quest'ultimo protegge fra due chunk, la
deadline copre uno stream che continua a produrre chunk oltre il budget.

Il testo parziale è accumulato incrementalmente e persistito via
``PartialCheckpoint``. Un checkpoint rifiutato solleva :class:`LeaseLost`
(vedi ``worker.py``): l'executor non intercetta, il worker si arrende
senza finalizzare. Un errore del runtime è tradotto in ``RunState.FAILED``
con ``finish_reason`` derivato dal codice di dominio, mai dal vendor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping

from newray.kernel.errors import (
    InferenceFailed,
    InferenceTimeout,
    ModelUnavailable,
)
from newray.modules.models import (
    ChatMessage,
    ChatModel,
    ChatRequest,
    ChatRole,
    Completion,
    ContentDelta,
)

from .durable import DurableRun, RunState
from .worker import PartialCheckpoint, RunOutcome


class ChatModelRunExecutor:
    """Consuma un run durevole con deadline complessiva.

    ``max_wall_seconds`` è il budget totale della generazione. Superata la
    soglia il run diventa ``INTERRUPTED`` con ``finish_reason='timeout'``:
    il worker persiste ciò che è arrivato al checkpoint prima del cutoff
    (§8.3 — troncamento non è successo).
    """

    def __init__(
        self,
        chat_model: ChatModel,
        *,
        max_wall_seconds: float = 300.0,
    ) -> None:
        if max_wall_seconds <= 0:
            raise ValueError("max_wall_seconds deve essere > 0")
        self._chat_model = chat_model
        self._max_wall_seconds = max_wall_seconds

    async def execute(
        self,
        run: DurableRun,
        checkpoint: PartialCheckpoint,
    ) -> RunOutcome:
        request = _reconstruct_chat_request(run)
        # Resume: la partial persistita in un tentativo precedente viene
        # riscritta prima del primo delta, così un consumatore che legge
        # snapshot dopo il restart continua a vedere il testo del vecchio
        # worker finché il nuovo produce.
        chunks: list[str] = [run.partial_text] if run.partial_text else []
        completion: Completion | None = None
        finish_reason: str = "stream_closed"
        state = RunState.FAILED

        stream = self._chat_model.stream(request)
        try:
            async with asyncio.timeout(self._max_wall_seconds):
                async for event in stream:
                    if isinstance(event, ContentDelta):
                        chunks.append(event.text)
                        # Il checkpoint può sollevare LeaseLost: si
                        # propaga fino al worker senza finalize.
                        await checkpoint.save("".join(chunks))
                    elif isinstance(event, Completion):
                        completion = event
                        break
            if completion is not None:
                finish_reason = completion.finish_reason
                state = RunState.COMPLETED
        except TimeoutError:
            finish_reason = "timeout"
            state = RunState.INTERRUPTED
        except ModelUnavailable as exc:
            finish_reason = f"model_unavailable: {exc.code}"
            state = RunState.FAILED
        except InferenceTimeout:
            finish_reason = "inference_timeout"
            state = RunState.FAILED
        except InferenceFailed as exc:
            finish_reason = f"inference_failed: {exc.code}"
            state = RunState.FAILED
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # noqa: BLE001
                    # aclose() non deve alterare l'esito: al più si logga.
                    pass

        return RunOutcome(
            state=state,
            finish_reason=finish_reason,
            partial_text="".join(chunks),
            prompt_tokens=completion.prompt_tokens if completion is not None else None,
            completion_tokens=completion.completion_tokens if completion is not None else None,
            eval_duration_ns=completion.eval_duration_ns if completion is not None else None,
        )


def _reconstruct_chat_request(run: DurableRun) -> ChatRequest:
    """Ricostruisce la ChatRequest dallo snapshot immutabile del run.

    Nessuna risoluzione lato server: model/runtime/messages sono già
    stati congelati alla creazione del run (P-02/P-05 prima slice).
    """
    snapshot = run.snapshot
    model = _require_str(snapshot, "model_name")
    runtime = _require_str(snapshot, "runtime")
    raw_messages = snapshot.get("messages")
    if not isinstance(raw_messages, Iterable):
        raise ValueError("snapshot: 'messages' non è iterabile")
    messages: list[ChatMessage] = []
    for item in raw_messages:
        role_raw = _require_str(item, "role")
        content = _require_str(item, "content")
        messages.append(ChatMessage(role=ChatRole(role_raw), content=content))
    if not messages:
        raise ValueError("snapshot: 'messages' vuoto")
    max_tokens_raw = snapshot.get("max_output_tokens")
    if max_tokens_raw is not None and not isinstance(max_tokens_raw, int):
        raise ValueError("snapshot: 'max_output_tokens' non intero")
    parameters_raw = snapshot.get("parameters", {})
    if not isinstance(parameters_raw, Mapping):
        raise ValueError("snapshot: 'parameters' non è mappabile")
    parameters: dict[str, object] = {str(k): v for k, v in parameters_raw.items()}
    return ChatRequest(
        model=model,
        runtime=runtime,
        messages=tuple(messages),
        parameters=parameters,
        max_tokens=max_tokens_raw,
        # Timeout di inattività dell'adapter: distinto dalla wall-deadline
        # dell'executor. Manteniamo il default dell'adapter (nessun
        # override dallo snapshot in P-05).
        inactivity_timeout=None,
    )


def _require_str(container: object, key: str) -> str:
    if not isinstance(container, Mapping):
        raise ValueError(f"snapshot: contenitore non mappabile per {key!r}")
    value = container.get(key)
    if not isinstance(value, str):
        raise ValueError(f"snapshot: {key!r} non è una stringa")
    return value


__all__ = ["ChatModelRunExecutor"]
