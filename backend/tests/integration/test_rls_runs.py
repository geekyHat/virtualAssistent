"""P-05: ricevuta concorrente e RLS dei run su PostgreSQL reale."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from conftest import DatabaseHandles
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from newray.infrastructure.database import create_engine
from newray.kernel.clock import SystemClock
from newray.kernel.errors import Conflict
from newray.kernel.identity import Scope
from newray.modules.conversations import ConversationService
from newray.modules.conversations.adapters.postgres import (
    PostgresConversationRepository,
    PostgresMessageStore,
)
from newray.modules.identity import IdentityService
from newray.modules.identity.adapters.postgres import (
    PostgresOwnerBootstrap,
    PostgresSessionStore,
    PostgresUserRepository,
)
from newray.modules.runs import DurableRun, RunState
from newray.modules.runs.adapters.postgres import PostgresRunStore


def _run(owner, conversation_id: uuid.UUID, *, key: str, payload_hash: str) -> DurableRun:
    now = datetime.now(UTC)
    return DurableRun(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        organization_id=owner.organization_id,
        owner_id=owner.user_id,
        idempotency_key=key,
        payload_hash=payload_hash,
        state=RunState.QUEUED,
        snapshot={"prompt": "prova", "model_name": "gemma:test", "digest": "sha256:test"},
        partial_text="",
        finish_reason=None,
        prompt_tokens=None,
        completion_tokens=None,
        eval_duration_ns=None,
        lease_owner=None,
        lease_until=None,
        fence=0,
        cancel_requested_at=None,
        created_at=now,
        updated_at=now,
    )


def test_ricevuta_concorrente_scope_e_cancellazione(test_databases: DatabaseHandles) -> None:
    engine = create_engine(test_databases.app)
    try:
        identity = IdentityService(
            PostgresUserRepository(engine),
            PostgresSessionStore(engine),
            SystemClock(),
            PostgresOwnerBootstrap(engine),
        )
        owner = identity.bootstrap_owner("Run owner", "test-passphrase-1234")
        conversations = ConversationService(
            PostgresConversationRepository(engine), PostgresMessageStore(engine), SystemClock()
        )
        conversation = conversations.create_conversation(owner, "P-05")
        store = PostgresRunStore(engine)
        attempts = [
            _run(
                owner,
                conversation.id,
                key="same-key",
                payload_hash="a" * 64,
            )
            for _ in range(2)
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(store.enqueue, attempts))
        assert results[0].id == results[1].id
        assert store.get(owner.scope, results[0].id) == results[0]
        assert store.get(Scope(owner.organization_id, uuid.uuid4()), results[0].id) is None
        with pytest.raises(ProgrammingError), engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.user_id', :v, true)"), {"v": str(owner.user_id)}
            )
            conn.execute(
                text("SELECT set_config('app.organization_id', :v, true)"),
                {"v": str(owner.organization_id)},
            )
            conn.execute(
                text("UPDATE runs SET snapshot = '{}'::jsonb WHERE id = :id"),
                {"id": results[0].id},
            )
        with pytest.raises(Conflict):
            store.enqueue(_run(owner, conversation.id, key="same-key", payload_hash="b" * 64))
        conversations.delete_conversation(owner, conversation.id)
        assert store.get(owner.scope, results[0].id) is None
    finally:
        engine.dispose()
