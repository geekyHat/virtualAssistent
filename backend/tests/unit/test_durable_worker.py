"""Unit test del worker P-05.

Verifica il ciclo di vita del worker con fake `RunStore` e `ChatModel`
in memoria: claim atomico, checkpoint del parziale, cancellazione onorata,
deadline wall-clock, fencing su lease scaduto e restart. La RLS reale è
provata sul PostgreSQL degli integration test.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from newray.kernel.identity import Principal, Scope, new_id
from newray.modules.conversations import Message, MessageRole
from newray.modules.models import ChatRequest, Completion, ContentDelta, StreamEvent
from newray.modules.runs import (
    TERMINAL_STATES,
    ClaimedRun,
    DurableRun,
    DurableRunWorker,
    RunState,
    WorkerConfig,
)
from newray.modules.runs.durable import LeaseLost, RunAlreadyTerminal


def _run_async(coro: object) -> None:
    asyncio.run(coro)  # type: ignore[arg-type]


class FakeRunStore:
    """RunStore in memoria con la stessa semantica di fencing dell'adapter."""

    def __init__(self) -> None:
        self._runs: dict[uuid.UUID, DurableRun] = {}

    def put(self, run: DurableRun) -> None:
        self._runs[run.id] = run

    def snapshot(self, run_id: uuid.UUID) -> DurableRun:
        return self._runs[run_id]

    def enqueue(self, run: DurableRun) -> DurableRun:  # pragma: no cover
        self._runs[run.id] = run
        return run

    def get(self, scope: Scope, run_id: uuid.UUID) -> DurableRun | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        if run.organization_id != scope.organization_id or run.owner_id != scope.user_id:
            return None
        return run

    def find_by_key(  # pragma: no cover
        self, scope: Scope, conversation_id: uuid.UUID, idempotency_key: str
    ) -> DurableRun | None:
        return None

    def claim_next(
        self, *, worker_id: uuid.UUID, lease_until: datetime, now: datetime
    ) -> ClaimedRun | None:
        for run in sorted(self._runs.values(), key=lambda r: r.created_at):
            claimable = run.state == RunState.QUEUED or (
                run.state == RunState.RUNNING
                and (run.lease_until is None or run.lease_until < now)
            )
            if claimable:
                updated = _replace(
                    run,
                    state=RunState.RUNNING,
                    lease_owner=worker_id,
                    lease_until=lease_until,
                    fence=run.fence + 1,
                    started_at=run.started_at or now,
                    heartbeat_at=now,
                    updated_at=now,
                )
                self._runs[run.id] = updated
                return ClaimedRun(run=updated, fence=updated.fence)
        return None

    def _check(self, run: DurableRun, worker_id: uuid.UUID, fence: int) -> None:
        if run.state in TERMINAL_STATES:
            raise RunAlreadyTerminal("terminal")
        if run.lease_owner != worker_id or run.fence != fence:
            raise LeaseLost("stale")

    def heartbeat(
        self, *, scope: Scope, run_id: uuid.UUID, worker_id: uuid.UUID,
        fence: int, lease_until: datetime, now: datetime,
    ) -> DurableRun:
        run = self._runs[run_id]
        self._check(run, worker_id, fence)
        updated = _replace(run, lease_until=lease_until, heartbeat_at=now, updated_at=now)
        self._runs[run_id] = updated
        return updated

    def checkpoint(
        self, *, scope: Scope, run_id: uuid.UUID, worker_id: uuid.UUID,
        fence: int, partial_text: str, lease_until: datetime, now: datetime,
    ) -> DurableRun:
        run = self._runs[run_id]
        self._check(run, worker_id, fence)
        updated = _replace(
            run, partial_text=partial_text, lease_until=lease_until,
            heartbeat_at=now, updated_at=now,
        )
        self._runs[run_id] = updated
        return updated

    def complete(
        self, *, scope: Scope, run_id: uuid.UUID, worker_id: uuid.UUID, fence: int,
        partial_text: str, finish_reason: str, prompt_tokens: int | None,
        completion_tokens: int | None, eval_duration_ns: int | None, now: datetime,
    ) -> DurableRun:
        run = self._runs[run_id]
        self._check(run, worker_id, fence)
        updated = _replace(
            run, state=RunState.COMPLETED, partial_text=partial_text,
            finish_reason=finish_reason, prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens, eval_duration_ns=eval_duration_ns,
            lease_owner=None, lease_until=None, finished_at=now,
            heartbeat_at=now, updated_at=now,
        )
        self._runs[run_id] = updated
        return updated

    def fail(
        self, *, scope: Scope, run_id: uuid.UUID, worker_id: uuid.UUID, fence: int,
        error_code: str, finish_reason: str, partial_text: str, now: datetime,
    ) -> DurableRun:
        run = self._runs[run_id]
        self._check(run, worker_id, fence)
        updated = _replace(
            run, state=RunState.FAILED, partial_text=partial_text,
            finish_reason=finish_reason, error_code=error_code,
            lease_owner=None, lease_until=None, finished_at=now,
            heartbeat_at=now, updated_at=now,
        )
        self._runs[run_id] = updated
        return updated

    def finalize_cancelled(
        self, *, scope: Scope, run_id: uuid.UUID, worker_id: uuid.UUID,
        fence: int, partial_text: str, now: datetime,
    ) -> DurableRun:
        run = self._runs[run_id]
        self._check(run, worker_id, fence)
        updated = _replace(
            run, state=RunState.CANCELLED, partial_text=partial_text,
            finish_reason="cancelled", lease_owner=None, lease_until=None,
            finished_at=now, heartbeat_at=now, updated_at=now,
        )
        self._runs[run_id] = updated
        return updated

    def interrupt_stale(  # pragma: no cover
        self, *, scope: Scope, run_id: uuid.UUID, worker_id: uuid.UUID,
        fence: int, now: datetime,
    ) -> DurableRun:
        run = self._runs[run_id]
        self._check(run, worker_id, fence)
        updated = _replace(
            run, state=RunState.INTERRUPTED, finish_reason="interrupted",
            lease_owner=None, lease_until=None, finished_at=now,
            heartbeat_at=now, updated_at=now,
        )
        self._runs[run_id] = updated
        return updated

    def mark_cancel_requested(
        self, scope: Scope, run_id: uuid.UUID, now: datetime
    ) -> DurableRun:
        run = self._runs[run_id]
        if run.state in TERMINAL_STATES or run.cancel_requested_at is not None:
            return run
        updated = _replace(run, cancel_requested_at=now, updated_at=now)
        self._runs[run_id] = updated
        return updated


def _replace(run: DurableRun, **changes: object) -> DurableRun:
    values: dict[str, object] = {
        "id": run.id, "conversation_id": run.conversation_id,
        "organization_id": run.organization_id, "owner_id": run.owner_id,
        "idempotency_key": run.idempotency_key, "payload_hash": run.payload_hash,
        "state": run.state, "snapshot": dict(run.snapshot),
        "partial_text": run.partial_text, "finish_reason": run.finish_reason,
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "eval_duration_ns": run.eval_duration_ns,
        "lease_owner": run.lease_owner, "lease_until": run.lease_until,
        "fence": run.fence, "created_at": run.created_at,
        "updated_at": run.updated_at, "deadline_at": run.deadline_at,
        "started_at": run.started_at, "finished_at": run.finished_at,
        "cancel_requested_at": run.cancel_requested_at,
        "heartbeat_at": run.heartbeat_at, "error_code": run.error_code,
    }
    values.update(changes)
    return DurableRun(**values)  # type: ignore[arg-type]


class ScriptedChatModel:
    def __init__(self, script: list[StreamEvent]) -> None:
        self._script = script
        self.requests: list[ChatRequest] = []
        self.aclose_called = 0

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        return self._iter(list(self._script))

    async def _iter(self, script: list[StreamEvent]) -> AsyncIterator[StreamEvent]:
        try:
            for event in script:
                yield event
        finally:
            self.aclose_called += 1


class RecordingConversations:
    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, str, str, str | None, str | None]] = []
        self.fail = False

    def add_exchange(
        self, principal: Principal, conversation_id: uuid.UUID,
        user_content: str, assistant_content: str, *,
        idempotency_key: str | None = None, request_hash: str | None = None,
    ) -> tuple[Message, Message]:
        if self.fail:
            raise RuntimeError("simulated persistence failure")
        self.calls.append(
            (conversation_id, user_content, assistant_content, idempotency_key, request_hash)
        )
        now = datetime.now(UTC)
        return (
            Message(id=new_id(), conversation_id=conversation_id, role=MessageRole.USER,
                    content=user_content, sequence=1, created_at=now),
            Message(id=new_id(), conversation_id=conversation_id, role=MessageRole.ASSISTANT,
                    content=assistant_content, sequence=2, created_at=now),
        )


def _payload_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _queued_run(*, deadline: datetime | None = None, prompt: str = "ciao") -> DurableRun:
    now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    snapshot: dict[str, object] = {
        "profile_id": str(new_id()),
        "profile_version_id": str(new_id()),
        "binding_id": str(new_id()),
        "model_name": "test-model",
        "runtime": "ollama",
        "digest": "sha256:deadbeef",
        "parameters": {},
        "instructions": None,
        "messages": [{"role": "user", "content": prompt}],
        "prompt": prompt,
        "max_output_tokens": 128,
        "context_truncated": False,
        "context_message_count": 1,
        "context_character_count": len(prompt),
        "request_hash": _payload_hash(prompt),
    }
    return DurableRun(
        id=new_id(), conversation_id=new_id(),
        organization_id=new_id(), owner_id=new_id(),
        idempotency_key="key-1",
        payload_hash=_payload_hash(json.dumps(snapshot, sort_keys=True)),
        state=RunState.QUEUED, snapshot=snapshot, partial_text="",
        finish_reason=None, prompt_tokens=None, completion_tokens=None,
        eval_duration_ns=None, lease_owner=None, lease_until=None,
        fence=0, created_at=now, updated_at=now, deadline_at=deadline,
    )


class TickingClock:
    def __init__(self, start: datetime, step: timedelta = timedelta(milliseconds=1)) -> None:
        self._now = start
        self._step = step

    def now(self) -> datetime:
        current = self._now
        self._now = self._now + self._step
        return current


def _make_worker(
    store: FakeRunStore,
    chat_model: object,
    conversations: RecordingConversations,
    *,
    config: WorkerConfig | None = None,
    clock: TickingClock | None = None,
    worker_id: uuid.UUID | None = None,
) -> DurableRunWorker:
    return DurableRunWorker(
        store=store,  # type: ignore[arg-type]
        chat_model=chat_model,  # type: ignore[arg-type]
        conversations=conversations,  # type: ignore[arg-type]
        config=config or WorkerConfig(
            poll_interval=0.01,
            lease_ttl=timedelta(seconds=30),
            heartbeat_interval=timedelta(seconds=10),
            checkpoint_min_interval=timedelta(0),
            cancel_check_interval=timedelta(milliseconds=50),
            inactivity_timeout=timedelta(seconds=60),
        ),
        clock=clock or TickingClock(datetime(2026, 9, 27, 12, 0, tzinfo=UTC)),
        worker_id=worker_id or new_id(),
    )


def test_worker_esegue_run_e_persiste_messaggi() -> None:
    async def scenario() -> None:
        store = FakeRunStore()
        run = _queued_run()
        store.put(run)
        chat = ScriptedChatModel([
            ContentDelta("Ciao"),
            ContentDelta(" mondo"),
            Completion(finish_reason="stop", prompt_tokens=3, completion_tokens=2,
                       eval_duration_ns=1_000_000),
        ])
        conversations = RecordingConversations()
        worker = _make_worker(store, chat, conversations)

        processed = await worker._tick()

        assert processed is True
        persisted = store.snapshot(run.id)
        assert persisted.state == RunState.COMPLETED
        assert persisted.partial_text == "Ciao mondo"
        assert persisted.prompt_tokens == 3
        assert persisted.completion_tokens == 2
        assert persisted.eval_duration_ns == 1_000_000
        assert persisted.finish_reason == "stop"
        assert persisted.lease_owner is None
        assert persisted.finished_at is not None
        assert conversations.calls == [
            (run.conversation_id, "ciao", "Ciao mondo", "key-1",
             str(run.snapshot["request_hash"]))
        ]
        assert chat.aclose_called == 1

    _run_async(scenario())


def test_worker_annulla_run_su_richiesta() -> None:
    async def scenario() -> None:
        store = FakeRunStore()
        run = _queued_run()
        store.put(run)

        class DelayedChat:
            def __init__(self) -> None:
                self.aclose_called = 0

            def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
                return self._delayed()

            async def _delayed(self) -> AsyncIterator[StreamEvent]:
                try:
                    for chunk in ("primo", " secondo"):
                        await asyncio.sleep(0.05)
                        yield ContentDelta(chunk)
                    yield Completion(finish_reason="stop", prompt_tokens=1, completion_tokens=2)
                finally:
                    self.aclose_called += 1

        chat = DelayedChat()
        conversations = RecordingConversations()
        worker = _make_worker(
            store, chat, conversations,
            config=WorkerConfig(
                poll_interval=0.01, lease_ttl=timedelta(seconds=30),
                heartbeat_interval=timedelta(seconds=10),
                checkpoint_min_interval=timedelta(0),
                cancel_check_interval=timedelta(milliseconds=10),
                inactivity_timeout=timedelta(seconds=60),
            ),
        )

        async def cancel_soon() -> None:
            await asyncio.sleep(0.02)
            store.mark_cancel_requested(
                Scope(run.organization_id, run.owner_id), run.id, datetime.now(UTC)
            )

        await asyncio.gather(worker._tick(), cancel_soon())

        persisted = store.snapshot(run.id)
        assert persisted.state == RunState.CANCELLED, (
            f"stato={persisted.state} err={persisted.error_code} finish={persisted.finish_reason}"
        )
        assert persisted.finish_reason == "cancelled"
        assert conversations.calls == []

    _run_async(scenario())


def test_worker_ferma_su_deadline_wall_clock() -> None:
    async def scenario() -> None:
        store = FakeRunStore()
        deadline = datetime(2026, 9, 27, 11, 0, tzinfo=UTC)
        run = _queued_run(deadline=deadline)
        store.put(run)

        class Endless:
            def __init__(self) -> None:
                self.aclose_called = 0

            def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
                return self._iter()

            async def _iter(self) -> AsyncIterator[StreamEvent]:
                try:
                    while True:
                        await asyncio.sleep(0.02)
                        yield ContentDelta(".")
                finally:
                    self.aclose_called += 1

        chat = Endless()
        conversations = RecordingConversations()
        worker = _make_worker(store, chat, conversations)

        await asyncio.wait_for(worker._tick(), timeout=2.0)

        persisted = store.snapshot(run.id)
        assert persisted.state == RunState.FAILED
        assert persisted.error_code == "INFERENCE_TIMEOUT"
        assert conversations.calls == []

    _run_async(scenario())


def test_claim_atomico_un_solo_worker_prende_run() -> None:
    store = FakeRunStore()
    run = _queued_run()
    store.put(run)

    now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    worker_a = new_id()
    worker_b = new_id()

    first = store.claim_next(worker_id=worker_a, lease_until=now + timedelta(seconds=30), now=now)
    second = store.claim_next(worker_id=worker_b, lease_until=now + timedelta(seconds=30), now=now)

    assert first is not None
    assert first.run.lease_owner == worker_a
    assert first.fence == 1
    assert second is None

    with pytest.raises(LeaseLost):
        store.checkpoint(
            scope=Scope(run.organization_id, run.owner_id),
            run_id=run.id, worker_id=new_id(), fence=0,
            partial_text="stale", lease_until=now, now=now,
        )


def test_lease_scaduto_permette_nuovo_claim_con_fence_avanzato() -> None:
    store = FakeRunStore()
    run = _queued_run()
    store.put(run)

    t0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    worker_a = new_id()
    first = store.claim_next(worker_id=worker_a, lease_until=t0 + timedelta(seconds=1), now=t0)
    assert first is not None and first.fence == 1

    t1 = t0 + timedelta(seconds=2)
    worker_b = new_id()
    second = store.claim_next(worker_id=worker_b, lease_until=t1 + timedelta(seconds=30), now=t1)
    assert second is not None
    assert second.run.lease_owner == worker_b
    assert second.fence == 2

    with pytest.raises(LeaseLost):
        store.complete(
            scope=Scope(run.organization_id, run.owner_id),
            run_id=run.id, worker_id=worker_a, fence=1,
            partial_text="x", finish_reason="stop",
            prompt_tokens=1, completion_tokens=1,
            eval_duration_ns=1, now=t1,
        )


def test_worker_registra_errore_su_snapshot_invalido() -> None:
    async def scenario() -> None:
        store = FakeRunStore()
        run = _queued_run()
        broken_snapshot = dict(run.snapshot)
        broken_snapshot["messages"] = []
        broken = _replace(run, snapshot=broken_snapshot)
        store.put(broken)
        chat = ScriptedChatModel([])
        conversations = RecordingConversations()
        worker = _make_worker(store, chat, conversations)

        await worker._tick()

        persisted = store.snapshot(run.id)
        assert persisted.state == RunState.FAILED
        assert persisted.error_code == "SNAPSHOT_INVALID"

    _run_async(scenario())


def test_worker_fallisce_se_persistenza_messaggi_va_male() -> None:
    async def scenario() -> None:
        store = FakeRunStore()
        run = _queued_run()
        store.put(run)
        chat = ScriptedChatModel([
            ContentDelta("out"),
            Completion(finish_reason="stop", prompt_tokens=1, completion_tokens=1),
        ])
        conversations = RecordingConversations()
        conversations.fail = True
        worker = _make_worker(store, chat, conversations)

        await worker._tick()

        persisted = store.snapshot(run.id)
        assert persisted.state == RunState.FAILED
        assert persisted.error_code == "PERSISTENCE_FAILED"

    _run_async(scenario())
