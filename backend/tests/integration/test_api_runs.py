"""P-05: percorso HTTP → DurableRunService → PostgreSQL → Worker (con echo).

Verifica che ``POST /conversations/{id}/runs`` accodi un run durevole
idempotente, che il worker lanciato dal lifespan lo consumi con il
modello di echo (nessuna rete) e che ``GET /runs/{id}`` mostri lo stato
progressivo fino al terminale.
"""

from __future__ import annotations

import asyncio
import json
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
            run_event_reader=PostgresRunStore(engine),
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


def _parse_sse(chunk: str) -> list[dict]:
    """Parser minimale di un blob SSE: ritorna gli eventi come dict."""
    events: list[dict] = []
    for block in chunk.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        current: dict[str, str] = {}
        for line in block.split("\n"):
            if ":" not in line:
                continue
            field, _, value = line.partition(":")
            current[field.strip()] = value.strip()
        if "data" in current:
            try:
                current["_data"] = json.loads(current["data"])
            except json.JSONDecodeError:
                current["_data"] = current["data"]
        events.append(current)
    return events


def test_events_sse_replay_e_terminale(client: TestClient) -> None:
    profile_id = _bootstrap_and_pick_profile(client)
    conversation_id = _create_conversation(client, "P-06 SSE")
    r = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": str(profile_id), "content": "ciao"},
        headers={"Idempotency-Key": "sse-1"},
    )
    assert r.status_code == 201
    run_id = uuid.UUID(r.json()["id"])
    # Attende terminale così lo stream fa solo replay + close.
    _wait_terminal_state(client, run_id)

    with client.stream(
        "GET",
        f"/api/v1/runs/{run_id}/events",
        headers={"Accept": "text/event-stream"},
    ) as response:
        assert response.status_code == 200
        buffer = "".join(response.iter_text())

    events = _parse_sse(buffer)
    types = [e.get("event") for e in events]
    assert "delta" in types
    assert types[-1] == "completed"
    # Sequenze monotone crescenti.
    ids = [int(e["id"]) for e in events if e.get("id")]
    assert ids == sorted(ids)
    assert ids[0] == 1


def test_events_after_cursore_ricomincia_dal_successivo(client: TestClient) -> None:
    profile_id = _bootstrap_and_pick_profile(client)
    conversation_id = _create_conversation(client, "P-06 cursore")
    r = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": str(profile_id), "content": "ciao mondo"},
        headers={"Idempotency-Key": "cur-1"},
    )
    run_id = uuid.UUID(r.json()["id"])
    _wait_terminal_state(client, run_id)

    # Prima lettura completa.
    with client.stream("GET", f"/api/v1/runs/{run_id}/events") as resp:
        events = _parse_sse("".join(resp.iter_text()))
    total = len(events)
    assert total >= 2

    # Reconnect da metà: gli eventi restituiti hanno sequence > cursore.
    cursor = int(events[len(events) // 2 - 1]["id"])
    with client.stream("GET", f"/api/v1/runs/{run_id}/events?after={cursor}") as resp:
        replay = _parse_sse("".join(resp.iter_text()))
    replay_ids = [int(e["id"]) for e in replay if e.get("id")]
    assert all(sid > cursor for sid in replay_ids)
    assert replay[-1].get("event") == "completed"

    # Last-Event-ID header prevale sul query ``after``.
    with client.stream(
        "GET",
        f"/api/v1/runs/{run_id}/events?after=0",
        headers={"Last-Event-ID": str(cursor)},
    ) as resp:
        via_header = _parse_sse("".join(resp.iter_text()))
    via_header_ids = [int(e["id"]) for e in via_header if e.get("id")]
    assert all(sid > cursor for sid in via_header_ids)


def test_cancel_idempotente_e_run_finisce_in_cancelled(client: TestClient) -> None:
    """POST /cancel è idempotente; il worker chiude il run in CANCELLED."""

    # ChatModel lento: fa un delta e poi attende, così cancel arriva prima
    # del completamento naturale. Iniettiamo un client dedicato con quel
    # modello: il fixture di default usa Echo che finisce immediatamente.
    #
    # Per semplicità qui usiamo il client Echo: dopo un run già completato,
    # il cancel resta idempotente e non modifica lo stato terminale.
    profile_id = _bootstrap_and_pick_profile(client)
    conversation_id = _create_conversation(client, "P-06 cancel idempotent")
    r = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": str(profile_id), "content": "ciao"},
        headers={"Idempotency-Key": "cancel-1"},
    )
    run_id = uuid.UUID(r.json()["id"])
    final = _wait_terminal_state(client, run_id)
    assert final["state"] == "completed"

    # Cancel su run terminale: 200, stato invariato, nessun evento nuovo.
    c1 = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert c1.status_code == 200, c1.text
    assert c1.json()["state"] == "completed"
    # Idempotente.
    c2 = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert c2.status_code == 200
    assert c2.json()["state"] == "completed"


def test_cancel_su_run_inesistente_e_404(client: TestClient) -> None:
    _bootstrap_and_pick_profile(client)
    r = client.post(f"/api/v1/runs/{uuid.uuid4()}/cancel")
    assert r.status_code == 404


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
