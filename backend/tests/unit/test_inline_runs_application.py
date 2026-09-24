"""Preview inline: orchestrazione, budget e persistenza senza framework."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from fakes import (
    FakeClock,
    InMemoryConversationRepository,
    InMemoryMessageStore,
    InMemoryModelBindingStore,
    InMemoryModelCatalog,
    InMemoryProfileDefaultsSeeder,
    InMemoryProfileRepository,
)
from newray.kernel.errors import Conflict, InferenceFailed
from newray.kernel.identity import Principal, Role
from newray.modules.conversations import ConversationService, MessageRole
from newray.modules.models import (
    RUNTIME_OLLAMA,
    ChatRequest,
    Completion,
    ContentDelta,
    ModelInfo,
    ModelStatus,
    StreamEvent,
)
from newray.modules.profiles import ProfileService
from newray.modules.runs import (
    INLINE_CONTEXT_MAX_MESSAGES,
    InlineCompleted,
    InlineDelta,
    InlineRunService,
)

NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)
MODEL = ModelInfo(
    name="newray-test:fixed",
    runtime=RUNTIME_OLLAMA,
    digest="sha256:test",
    status=ModelStatus.QUALIFIED,
    capabilities=("chat",),
)


class ControlledModel:
    def __init__(self, *, finish_reason: str = "stop", fail: bool = False) -> None:
        self.finish_reason = finish_reason
        self.fail = fail
        self.requests: list[ChatRequest] = []
        self.closed = 0

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)

        async def generate() -> AsyncIterator[StreamEvent]:
            try:
                yield ContentDelta("risposta")
                if self.fail:
                    raise InferenceFailed("guasto controllato")
                yield Completion(
                    finish_reason=self.finish_reason,
                    prompt_tokens=7,
                    completion_tokens=4,
                    eval_duration_ns=1_000_000_000,
                )
            finally:
                self.closed += 1

        return generate()


class EmptyModel:
    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        async def generate() -> AsyncIterator[StreamEvent]:
            return
            yield  # pragma: no cover

        return generate()


class CancellableModel:
    def __init__(self) -> None:
        self.waiting = asyncio.Event()
        self.closed = 0

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        async def generate() -> AsyncIterator[StreamEvent]:
            try:
                yield ContentDelta("parziale")
                self.waiting.set()
                await asyncio.Event().wait()
            finally:
                self.closed += 1

        return generate()


def _system(
    model: object,
) -> tuple[Principal, ConversationService, ProfileService, InlineRunService]:
    principal = Principal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), Role.OWNER)
    conversations_repo = InMemoryConversationRepository()
    conversations = ConversationService(
        conversations_repo,
        InMemoryMessageStore(conversations_repo),
        FakeClock(NOW),
    )
    profiles_repo = InMemoryProfileRepository()
    bindings = InMemoryModelBindingStore()
    profiles = ProfileService(
        profiles_repo,
        bindings,
        InMemoryModelCatalog((MODEL,)),
        FakeClock(NOW),
        InMemoryProfileDefaultsSeeder(profiles_repo, bindings),
        default_model_name=MODEL.name,
    )
    return (
        principal,
        conversations,
        profiles,
        InlineRunService(
            conversations,
            profiles,
            model,  # type: ignore[arg-type]
        ),
    )


async def _prepared(
    model: object, content: str = "domanda"
) -> tuple[Principal, ConversationService, InlineRunService, object, object]:
    principal, conversations, profiles, runs = _system(model)
    conversation = conversations.create_conversation(principal, "Test")
    await profiles.provision_default(principal)
    profile = (await profiles.list_profiles(principal))[0]
    prepared = await runs.prepare(principal, conversation.id, profile.profile.id, content)
    return principal, conversations, runs, conversation, prepared


def test_successo_persiste_scambio_atomico_e_metrica() -> None:
    async def scenario() -> None:
        model = ControlledModel()
        principal, conversations, runs, conversation, prepared = await _prepared(model)
        events = [
            event
            async for event in runs.execute(
                principal,
                prepared,
                idempotency_key="request-1",  # type: ignore[arg-type]
            )
        ]
        assert events[0] == InlineDelta("risposta")
        completed = events[-1]
        assert isinstance(completed, InlineCompleted)
        assert completed.completion is not None
        assert completed.completion.tokens_per_second == 4.0
        messages = conversations.list_messages(
            principal,
            conversation.id,
            limit=10,  # type: ignore[union-attr]
        )
        assert [(m.role, m.content) for m in messages] == [
            (MessageRole.USER, "domanda"),
            (MessageRole.ASSISTANT, "risposta"),
        ]
        assert model.closed == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("finish_reason", ["stop", "length"])
def test_terminali_reali_restano_distinti(finish_reason: str) -> None:
    async def scenario() -> None:
        principal, _, runs, _, prepared = await _prepared(
            ControlledModel(finish_reason=finish_reason)
        )
        events = [
            event
            async for event in runs.execute(
                principal,
                prepared,
                idempotency_key=None,  # type: ignore[arg-type]
            )
        ]
        completed = events[-1]
        assert isinstance(completed, InlineCompleted)
        assert completed.completion is not None
        assert completed.completion.finish_reason == finish_reason

    asyncio.run(scenario())


def test_guasto_o_eof_non_lasciano_prompt_orfano() -> None:
    async def scenario(model: object) -> None:
        principal, conversations, runs, conversation, prepared = await _prepared(model)
        with pytest.raises(InferenceFailed):
            _ = [
                event
                async for event in runs.execute(
                    principal,
                    prepared,
                    idempotency_key="failure",  # type: ignore[arg-type]
                )
            ]
        assert (
            conversations.list_messages(
                principal,
                conversation.id,
                limit=10,  # type: ignore[union-attr]
            )
            == []
        )

    asyncio.run(scenario(ControlledModel(fail=True)))
    asyncio.run(scenario(EmptyModel()))


def test_cancel_chiude_lo_stream_e_non_lascia_prompt_orfano() -> None:
    async def scenario() -> None:
        model = CancellableModel()
        principal, conversations, runs, conversation, prepared = await _prepared(model)

        async def consume() -> None:
            _ = [
                event
                async for event in runs.execute(
                    principal,
                    prepared,
                    idempotency_key="cancelled",  # type: ignore[arg-type]
                )
            ]

        task = asyncio.create_task(consume())
        await model.waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert model.closed == 1
        assert (
            conversations.list_messages(
                principal,
                conversation.id,
                limit=10,  # type: ignore[union-attr]
            )
            == []
        )

    asyncio.run(scenario())


def test_ricevuta_riproduce_output_senza_seconda_inference() -> None:
    async def scenario() -> None:
        model = ControlledModel()
        principal, conversations, runs, conversation, prepared = await _prepared(model)
        first = [
            event
            async for event in runs.execute(
                principal,
                prepared,
                idempotency_key="same-key",  # type: ignore[arg-type]
            )
        ]
        replay_prepared = await runs.prepare(
            principal,
            conversation.id,  # type: ignore[union-attr]
            prepared.binding.profile_id,  # type: ignore[union-attr]
            "domanda",
        )
        replay = [
            event
            async for event in runs.execute(principal, replay_prepared, idempotency_key="same-key")
        ]
        assert len(model.requests) == 1
        assert first[0] == replay[0] == InlineDelta("risposta")
        assert isinstance(replay[-1], InlineCompleted)
        assert replay[-1].replayed is True
        assert (
            len(
                conversations.list_messages(
                    principal,
                    conversation.id,
                    limit=10,  # type: ignore[union-attr]
                )
            )
            == 2
        )

    asyncio.run(scenario())


def test_stessa_chiave_con_prompt_diverso_è_conflitto() -> None:
    async def scenario() -> None:
        model = ControlledModel()
        principal, _, runs, conversation, prepared = await _prepared(model)
        _ = [
            event
            async for event in runs.execute(
                principal,
                prepared,
                idempotency_key="same-key",  # type: ignore[arg-type]
            )
        ]
        changed = await runs.prepare(
            principal,
            conversation.id,  # type: ignore[union-attr]
            prepared.binding.profile_id,  # type: ignore[union-attr]
            "altra domanda",
        )
        with pytest.raises(Conflict):
            _ = [
                event
                async for event in runs.execute(principal, changed, idempotency_key="same-key")
            ]
        assert len(model.requests) == 1

    asyncio.run(scenario())


def test_budget_contesto_usa_i_messaggi_più_recenti_e_dichiara_il_taglio() -> None:
    async def scenario() -> None:
        model = ControlledModel()
        principal, conversations, profiles, runs = _system(model)
        conversation = conversations.create_conversation(principal, "Lunga")
        for index in range(INLINE_CONTEXT_MAX_MESSAGES + 5):
            conversations.add_user_message(principal, conversation.id, f"m-{index}")
        await profiles.provision_default(principal)
        profile = (await profiles.list_profiles(principal))[0]
        prepared = await runs.prepare(principal, conversation.id, profile.profile.id, "nuova")
        assert prepared.context_truncated is True
        contents = [message.content for message in prepared.chat_request.messages]
        assert "m-0" not in contents
        assert f"m-{INLINE_CONTEXT_MAX_MESSAGES + 4}" in contents
        assert contents[-1] == "nuova"
        assert prepared.chat_request.max_tokens == 2_048

    asyncio.run(scenario())
