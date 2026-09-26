"""P-05: percorso HTTP → DurableRunService → PostgreSQL → Worker (con echo).

Verifica che ``POST /conversations/{id}/runs`` accodi un run durevole
idempotente, che il worker lanciato dal lifespan lo consumi con il
modello di echo (nessuna rete) e che ``GET /runs/{id}`` mostri lo stato
progressivo fino al terminale.
"""

from __future__ import annotations

import asyncio
import uuid

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


@pytest.fixture()
def client(test_databases: DatabaseHandles):
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
    # Il resolver del binding esige un modello presente nel catalogo:
    # forniamo una lista in memoria coerente col default del pilot.
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
        ),
        headers={"Origin": "http://testserver"},
    ) as test_client:
        yield test_client
    engine.dispose()


def _bootstrap_and_pick_profile(client: TestClient) -> uuid.UUID:
    """Prova il flow completo: crea owner, seedd profili, ritorna Assistant."""
    r = client.post(
        "/api/v1/session",
        json={"display_name": "Ada", "credential": "test-passphrase-1234"},
    )
    assert r.status_code == 201
    # Il seeding dei profili di default è idempotente su POST.
    r = client.post("/api/v1/profiles/defaults")
    assert r.status_code in {200, 201}
    r = client.get("/api/v1/profiles")
    assert r.status_code == 200
    profiles = r.json()["items"]
    assistant = next(p for p in profiles if p["kind"] == "assistant")
    return uuid.UUID(assistant["id"])


def _create_conversation(client: TestClient, title: str) -> uuid.UUID:
    r = client.post("/api/v1/conversations", json={"title": title})
    assert r.status_code == 201
    return uuid.UUID(r.json()["id"])


def _wait_terminal_state(
    client: TestClient, run_id: uuid.UUID, timeout_seconds: float = 5.0
) -> dict:
    """Poll di ``GET /runs/{id}`` fino a stato terminale o timeout."""
    terminal = {"completed", "failed", "cancelled", "interrupted"}

    async def _poll() -> dict:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            r = client.get(f"/api/v1/runs/{run_id}")
            assert r.status_code == 200
            body = r.json()
            if body["state"] in terminal:
                return body
            await asyncio.sleep(0.05)
        raise AssertionError(f"run non terminale entro {timeout_seconds}s: {body}")

    return asyncio.run(_poll())


def test_post_run_idempotente_e_worker_esegue_echo(client: TestClient) -> None:
    profile_id = _bootstrap_and_pick_profile(client)
    conversation_id = _create_conversation(client, "P-05 API run")

    # POST senza Idempotency-Key → 422 (obbligatoria).
    r = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": str(profile_id), "content": "ciao"},
    )
    assert r.status_code == 422

    headers = {"Idempotency-Key": "run-1"}
    r = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": str(profile_id), "content": "ciao"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] in {"queued", "running", "completed"}
    assert body["conversation_id"] == str(conversation_id)
    run_id = uuid.UUID(body["id"])
    location = r.headers.get("Location")
    assert location and location.endswith(f"/runs/{run_id}")

    # Stessa chiave, stesso payload → stesso run id (dedup ricevuta).
    r2 = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": str(profile_id), "content": "ciao"},
        headers=headers,
    )
    assert r2.status_code == 201
    assert r2.json()["id"] == body["id"]

    # Stessa chiave, payload diverso → 409 CONFLICT.
    r3 = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": str(profile_id), "content": "altro"},
        headers=headers,
    )
    assert r3.status_code == 409
    assert r3.json()["code"] == "CONFLICT"

    # Il worker consuma la coda: attendiamo lo stato terminale.
    final = _wait_terminal_state(client, run_id)
    assert final["state"] == "completed"
    assert final["finish_reason"] == "stop"
    assert final["partial_text"].startswith("Echo:")
    assert final["model_name"] == "llama3.1"


def test_get_run_di_altro_scope_non_leggibile(client: TestClient) -> None:
    profile_id = _bootstrap_and_pick_profile(client)
    conversation_id = _create_conversation(client, "P-05 API run RLS")
    r = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": str(profile_id), "content": "hola"},
        headers={"Idempotency-Key": "rls-1"},
    )
    assert r.status_code == 201
    run_id = uuid.UUID(r.json()["id"])
    # Attesa terminale per non lasciare run in coda dopo il logout.
    _wait_terminal_state(client, run_id)

    # Logout → chi cerca lo stesso run senza cookie: 401.
    client.post("/api/v1/session/revoke")
    client.cookies.clear()
    r = client.get(f"/api/v1/runs/{run_id}")
    assert r.status_code == 401
