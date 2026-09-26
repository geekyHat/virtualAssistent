"""Fixture condivise per i test avversariali (P-17).

Usa il DB reale (RLS FORCE, ruolo applicativo non owner). Il file
riesporta `test_databases` dal conftest di ``tests/integration`` e
costruisce un ``TestClient`` con `EchoChatModel` (nessuna rete),
`DurableRunService` e worker durevoli — così i test coprono API,
DB, cascata di RLS e worker senza dipendere da GPU o Ollama.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import DatabaseHandles
from fastapi.testclient import TestClient

from newray.bootstrap.api import create_app
from newray.infrastructure.database import create_engine
from newray.kernel.clock import SystemClock
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
from newray.modules.models import ModelInfo, ModelStatus
from newray.modules.models.adapters.echo import EchoChatModel
from newray.modules.profiles import ProfileService
from newray.modules.profiles.adapters.postgres import (
    PostgresModelBindingStore,
    PostgresProfileDefaultsSeeder,
    PostgresProfileRepository,
)
from newray.modules.runs import (
    ChatModelRunExecutor,
    DurableRunService,
    InlineRunService,
    RunLauncher,
    Worker,
)
from newray.modules.runs.adapters.postgres import PostgresRunStore

# Rende importabile il pacchetto ``fakes`` dei test (per InMemoryModelCatalog).
_TESTS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_TESTS_ROOT))
from fakes import InMemoryModelCatalog  # noqa: E402


@pytest.fixture()
def hostile_client(test_databases: DatabaseHandles) -> Iterator[TestClient]:
    """Client con app completa; il chiamante gestisce i cookie a mano."""
    engine = create_engine(test_databases.app)
    identity = IdentityService(
        PostgresUserRepository(engine),
        PostgresSessionStore(engine),
        SystemClock(),
        PostgresOwnerBootstrap(engine),
    )
    conversations = ConversationService(
        PostgresConversationRepository(engine),
        PostgresMessageStore(engine),
        SystemClock(),
    )
    catalog = InMemoryModelCatalog(
        (
            ModelInfo(
                name="llama3.1",
                runtime="ollama",
                digest="sha256:test",
                status=ModelStatus.INSTALLED,
                capabilities=("chat",),
            ),
        )
    )
    profiles = ProfileService(
        PostgresProfileRepository(engine),
        PostgresModelBindingStore(engine),
        catalog,
        SystemClock(),
        PostgresProfileDefaultsSeeder(engine),
        default_model_name="llama3.1",
    )
    chat_model = EchoChatModel()
    inline_run = InlineRunService(conversations, profiles, chat_model)
    durable_run = DurableRunService(PostgresRunStore(engine), inline_run)
    worker = Worker(
        PostgresRunStore(engine),
        ChatModelRunExecutor(chat_model, max_wall_seconds=10.0),
        SystemClock(),
        lease_duration_seconds=30,
        heartbeat_seconds=5.0,
    )
    launcher = RunLauncher(worker, idle_backoff_seconds=0.05)
    with TestClient(
        create_app(
            identity,
            conversations,
            profiles,
            cookie_secure=False,
            chat_model=chat_model,
            durable_run_service=durable_run,
            run_launcher=launcher,
            run_event_reader=PostgresRunStore(engine),
        ),
        headers={"Origin": "http://testserver"},
    ) as test_client:
        # Il TestClient di default gestisce cookie automaticamente:
        # nei test avversariali serve controllo esplicito.
        test_client.follow_redirects = False
        yield test_client
    engine.dispose()


def bootstrap_owner(client: TestClient, name: str, passphrase: str) -> str:
    """Bootstrap del proprietario. Ritorna il cookie di sessione."""
    r = client.post(
        "/api/v1/session",
        json={"display_name": name, "credential": passphrase},
    )
    assert r.status_code == 201, r.text
    return r.headers["set-cookie"].split(";")[0].split("=", 1)[1]


def create_conversation(client: TestClient, title: str) -> uuid.UUID:
    r = client.post("/api/v1/conversations", json={"title": title})
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


def seed_assistant_profile(client: TestClient) -> uuid.UUID:
    client.post("/api/v1/profiles/defaults")
    profiles = client.get("/api/v1/profiles").json()["items"]
    assistant = next(p for p in profiles if p["kind"] == "assistant")
    return uuid.UUID(assistant["id"])
