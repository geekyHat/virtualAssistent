"""P-05: bridge ChatModel → RunOutcome con deadline, resume e mapping errori."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from newray.kernel.errors import InferenceFailed, ModelUnavailable
from newray.modules.models import (
    ChatRequest,
    Completion,
    ContentDelta,
    StreamEvent,
)
from newray.modules.runs import (
    ChatModelRunExecutor,
    DurableRun,
    RunState,
)
from newray.modules.runs.worker import PartialCheckpoint


class _NullCheckpoint:
    async def save(self, delta_text: str, partial_text: str) -> None:  # noqa: ARG002
        return None


@dataclass
class _RecordingCheckpoint(PartialCheckpoint):
    saved: list[tuple[str, str]] = field(default_factory=list)

    async def save(self, delta_text: str, partial_text: str) -> None:
        self.saved.append((delta_text, partial_text))


def _snapshot(text: str = "ciao") -> dict[str, object]:
    return {
        "model_name": "gemma:test",
        "runtime": "ollama",
        "messages": [{"role": "user", "content": text}],
        "parameters": {},
        "max_output_tokens": 64,
    }


def _run(snapshot: dict[str, object] | None = None, partial: str = "") -> DurableRun:
    now = datetime.now(UTC)
    return DurableRun(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        idempotency_key="k",
        payload_hash="a" * 64,
        state=RunState.RUNNING,
        snapshot=snapshot or _snapshot(),
        partial_text=partial,
        finish_reason=None,
        prompt_tokens=None,
        completion_tokens=None,
        eval_duration_ns=None,
        lease_owner=uuid.uuid4(),
        lease_until=now,
        fence=1,
        cancel_requested_at=None,
        created_at=now,
        updated_at=now,
    )


class _ScriptedModel:
    """ChatModel di test: rilascia eventi predefiniti in sequenza."""

    def __init__(self, events: list[StreamEvent]) -> None:
        self._events = events
        self.seen_request: ChatRequest | None = None

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.seen_request = request
        for event in self._events:
            yield event


def test_completa_run_con_completion_dello_stream() -> None:
    model = _ScriptedModel(
        [
            ContentDelta(text="ciao "),
            ContentDelta(text="mondo"),
            Completion(
                finish_reason="stop",
                prompt_tokens=3,
                completion_tokens=7,
                eval_duration_ns=1_000_000,
            ),
        ]
    )
    executor = ChatModelRunExecutor(model)
    checkpoint = _RecordingCheckpoint()
    outcome = asyncio.run(executor.execute(_run(), checkpoint))
    assert outcome.state == RunState.COMPLETED
    assert outcome.finish_reason == "stop"
    assert outcome.partial_text == "ciao mondo"
    assert outcome.prompt_tokens == 3
    assert outcome.completion_tokens == 7
    assert outcome.eval_duration_ns == 1_000_000
    # Ogni delta produce un checkpoint incrementale.
    assert checkpoint.saved == [("ciao ", "ciao "), ("mondo", "ciao mondo")]
    # La ChatRequest è stata ricostruita dallo snapshot.
    assert model.seen_request is not None
    assert model.seen_request.model == "gemma:test"


def test_stream_chiuso_senza_completion_produce_failed() -> None:
    model = _ScriptedModel([ContentDelta(text="parziale")])
    executor = ChatModelRunExecutor(model)
    outcome = asyncio.run(executor.execute(_run(), _NullCheckpoint()))
    assert outcome.state == RunState.FAILED
    assert outcome.finish_reason == "stream_closed"
    assert outcome.partial_text == "parziale"
    assert outcome.completion_tokens is None


def test_resume_da_partial_precedente() -> None:
    model = _ScriptedModel(
        [
            ContentDelta(text=" e continua"),
            Completion(finish_reason="stop", prompt_tokens=1, completion_tokens=2),
        ]
    )
    executor = ChatModelRunExecutor(model)
    checkpoint = _RecordingCheckpoint()
    outcome = asyncio.run(executor.execute(_run(partial="già scritto"), checkpoint))
    assert outcome.state == RunState.COMPLETED
    assert outcome.partial_text == "già scritto e continua"
    assert checkpoint.saved == [(" e continua", "già scritto e continua")]


def test_deadline_wall_scaduta_produce_interrupted_con_partial() -> None:
    class SlowModel:
        async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
            yield ContentDelta(text="uno ")
            # Sleep abbastanza da superare il budget di 50 ms.
            await asyncio.sleep(1.0)
            yield Completion(finish_reason="stop", prompt_tokens=None, completion_tokens=None)

    executor = ChatModelRunExecutor(SlowModel(), max_wall_seconds=0.05)
    checkpoint = _RecordingCheckpoint()
    outcome = asyncio.run(executor.execute(_run(), checkpoint))
    assert outcome.state == RunState.INTERRUPTED
    assert outcome.finish_reason == "timeout"
    assert outcome.partial_text == "uno "
    assert outcome.completion_tokens is None


def test_inference_failed_mappato_a_failed() -> None:
    class BrokenModel:
        async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
            yield ContentDelta(text="prima ")
            raise InferenceFailed("runtime rifiuta")

    executor = ChatModelRunExecutor(BrokenModel())
    outcome = asyncio.run(executor.execute(_run(), _NullCheckpoint()))
    assert outcome.state == RunState.FAILED
    assert outcome.finish_reason.startswith("inference_failed:")
    assert outcome.partial_text == "prima "


def test_model_unavailable_mappato_a_failed() -> None:
    class Missing:
        async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
            raise ModelUnavailable("nessun binding")
            yield  # unreachable, forza async generator

    executor = ChatModelRunExecutor(Missing())
    outcome = asyncio.run(executor.execute(_run(), _NullCheckpoint()))
    assert outcome.state == RunState.FAILED
    assert outcome.finish_reason.startswith("model_unavailable:")


def test_checkpoint_leaselost_si_propaga_al_worker() -> None:
    from newray.modules.runs.worker import LeaseLost

    class Model:
        async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
            yield ContentDelta(text="x")

    class HostileCheckpoint:
        async def save(self, delta_text: str, partial_text: str) -> None:  # noqa: ARG002
            raise LeaseLost("fence bumped")

    executor = ChatModelRunExecutor(Model())
    with pytest.raises(LeaseLost):
        asyncio.run(executor.execute(_run(), HostileCheckpoint()))


def test_costruttore_rifiuta_wall_seconds_non_positivo() -> None:
    with pytest.raises(ValueError):
        ChatModelRunExecutor(_ScriptedModel([]), max_wall_seconds=0)
