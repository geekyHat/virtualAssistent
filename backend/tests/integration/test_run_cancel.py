"""P-06: cancellazione cooperativa durante l'inferenza.

Il modello di test è "lento": emette un delta e poi attende un evento
esterno. Il test:

1. Accoda un run, lascia partire il worker.
2. Aspetta il primo delta persistito (evento visibile via API).
3. Chiama ``POST /runs/{id}/cancel``.
4. Sblocca il modello: al checkpoint successivo il worker rileva la
   richiesta e finalizza in ``CANCELLED``.
5. La sequenza SSE contiene ``cancel_requested`` e termina con ``cancelled``.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import AsyncIterator

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
from newray.modules.models import (
    ChatRequest,
    Completion,
    ContentDelta,
    ModelInfo,
    ModelStatus,
    StreamEvent,
)
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


class _GatedModel:
    """Emette un delta, attende un evento, poi Completion.

    Serve per aprire una finestra deterministica in cui il test può
    invocare cancel prima che l'esecuzione finisca da sola.
    """

    def __init__(self) -> None:
        self.first_delta_out = threading.Event()
        self.release = threading.Event()

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        yield ContentDelta(text="uno ")
        self.first_delta_out.set()
        # Non blocca l'event loop: aspetta il flag su un thread.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.release.wait, 10.0)
        yield ContentDelta(text="due")
        yield Completion(finish_reason="stop", prompt_tokens=1, completion_tokens=2)


@pytest.fixture()
def gated_client(test_databases: DatabaseHandles):
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

    import sys as _sys

    _sys.path.insert(0, "tests")
    from fakes import InMemoryModelCatalog  # noqa: PLC0415

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
    chat_model = _GatedModel()
    inline_run = InlineRunService(conversations, profiles, chat_model)
    durable_run = DurableRunService(PostgresRunStore(engine), inline_run)
    worker = Worker(
        PostgresRunStore(engine),
        ChatModelRunExecutor(chat_model, max_wall_seconds=15.0),
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
        yield test_client, chat_model
    engine.dispose()


def _bootstrap(client: TestClient) -> uuid.UUID:
    client.post(
        "/api/v1/session",
        json={"display_name": "Ada", "credential": "test-passphrase-1234"},
    )
    client.post("/api/v1/profiles/defaults")
    profiles = client.get("/api/v1/profiles").json()["items"]
    return uuid.UUID(next(p for p in profiles if p["kind"] == "assistant")["id"])


def test_cancel_durante_inference_produce_cancelled(gated_client) -> None:
    client, chat_model = gated_client
    profile_id = _bootstrap(client)
    conv = client.post("/api/v1/conversations", json={"title": "P-06 cancel"})
    conversation_id = uuid.UUID(conv.json()["id"])

    r = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": str(profile_id), "content": "hola"},
        headers={"Idempotency-Key": "cancel-live-1"},
    )
    assert r.status_code == 201
    run_id = uuid.UUID(r.json()["id"])

    # Attende che il worker abbia emesso e persistito il primo delta.
    assert chat_model.first_delta_out.wait(timeout=5.0)

    # Cancella mentre l'executor è ancora bloccato.
    c = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert c.status_code == 200
    assert c.json()["cancel_requested_at"] is not None

    # Sblocca l'executor: al prossimo checkpoint rileva cancel e finalizza.
    chat_model.release.set()

    async def _poll() -> dict:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        last: dict = {}
        while loop.time() < deadline:
            r = client.get(f"/api/v1/runs/{run_id}")
            last = r.json()
            if last["state"] in {"cancelled", "completed", "failed", "interrupted"}:
                return last
            await asyncio.sleep(0.05)
        raise AssertionError(f"run non terminale: {last}")

    final = asyncio.run(_poll())
    assert final["state"] == "cancelled"
    assert final["finish_reason"] == "cancelled"
    assert final["partial_text"].startswith("uno")

    # Lo stream SSE include cancel_requested e chiude con cancelled.
    with client.stream("GET", f"/api/v1/runs/{run_id}/events") as resp:
        chunk = "".join(resp.iter_text())
    types: list[str] = []
    for block in chunk.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("event: "):
                types.append(line[len("event: ") :])
    assert "delta" in types
    assert "cancel_requested" in types
    assert types[-1] == "cancelled"
