"""P-05: contratto di creazione idempotente e snapshot immutabile."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from newray.kernel.errors import Conflict, NotFound
from newray.kernel.identity import Principal, Role, Scope
from newray.modules.models import ChatMessage, ChatRequest, ChatRole
from newray.modules.profiles import ResolvedBinding
from newray.modules.runs import DurableRun, DurableRunService, PreparedInlineRun


class FakeStore:
    def __init__(self) -> None:
        self.by_key: dict[tuple[Scope, uuid.UUID, str], DurableRun] = {}
        self.insertions = 0

    def enqueue(self, run: DurableRun) -> DurableRun:
        key = (Scope(run.organization_id, run.owner_id), run.conversation_id, run.idempotency_key)
        existing = self.by_key.get(key)
        if existing is not None:
            if existing.payload_hash != run.payload_hash:
                raise Conflict("chiave riutilizzata")
            return existing
        self.by_key[key] = run
        self.insertions += 1
        return run

    def find_by_key(
        self, scope: Scope, conversation_id: uuid.UUID, idempotency_key: str
    ) -> DurableRun | None:
        return self.by_key.get((scope, conversation_id, idempotency_key))

    def get(self, scope: Scope, run_id: uuid.UUID) -> DurableRun | None:
        return next(
            (
                run
                for (owner, _, _), run in self.by_key.items()
                if owner == scope and run.id == run_id
            ),
            None,
        )


class FakeInline:
    def __init__(self) -> None:
        self.prepares = 0

    async def prepare(
        self, principal: Principal, conversation_id: uuid.UUID, profile_id: uuid.UUID, content: str
    ) -> PreparedInlineRun:
        self.prepares += 1
        binding = ResolvedBinding(
            profile_id=profile_id,
            profile_version_id=uuid.uuid4(),
            binding_id=uuid.uuid4(),
            profile_version="1.0.0",
            binding_name="local-assistant",
            runtime="ollama",
            model_name="gemma:test",
            digest="sha256:fixed",
            parameters={"options": {"temperature": 0}},
            capabilities=("chat",),
            instructions="Istruzioni",
        )
        return PreparedInlineRun(
            conversation_id=conversation_id,
            content=content,
            binding=binding,
            chat_request=ChatRequest(
                model=binding.model_name,
                runtime=binding.runtime,
                messages=(
                    ChatMessage(ChatRole.SYSTEM, binding.instructions),
                    ChatMessage(ChatRole.USER, content),
                ),
                parameters=dict(binding.parameters),
                max_tokens=2048,
            ),
            request_hash="unused",
            context_message_count=2,
            context_character_count=len(content) + len(binding.instructions),
            context_truncated=False,
        )


def test_creazione_replay_conflitto_e_scope() -> None:
    async def scenario() -> None:
        principal = Principal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), Role.OWNER)
        other = Principal(uuid.uuid4(), principal.organization_id, uuid.uuid4(), Role.MEMBER)
        conversation_id = uuid.uuid4()
        profile_id = uuid.uuid4()
        store = FakeStore()
        inline = FakeInline()
        service = DurableRunService(store, inline)  # type: ignore[arg-type]

        first = await service.create(principal, conversation_id, profile_id, "Ciao", "key-1")
        replay = await service.create(principal, conversation_id, profile_id, "Ciao", "key-1")
        assert first.id == replay.id
        assert first.state.value == "queued"
        assert first.snapshot["digest"] == "sha256:fixed"
        assert list(first.snapshot["messages"]) == [  # type: ignore[call-overload]
            {"role": "system", "content": "Istruzioni"},
            {"role": "user", "content": "Ciao"},
        ]
        with pytest.raises(TypeError):
            first.snapshot["prompt"] = "mutato"  # type: ignore[index]
        assert store.insertions == 1
        assert inline.prepares == 1
        with pytest.raises(Conflict):
            await service.create(principal, conversation_id, profile_id, "Diverso", "key-1")
        with pytest.raises(NotFound):
            await service.get(other, first.id)

    asyncio.run(scenario())
