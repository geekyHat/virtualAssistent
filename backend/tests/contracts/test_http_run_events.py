"""Contratto HTTP degli eventi/cancel dei run durevoli (P-06, NewRay.md §19.3).

``InMemoryRunStore`` è manipolato direttamente per portare i run in stati
già terminali prima della richiesta: lo stream si chiude da solo dopo un
giro, senza bisogno di un worker reale né di un client che tronca la
connessione (il poll infinito su un run mai terminale è provato a un
livello più basso, non qui).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from fakes import (
    FakeClock,
    InMemoryConversationRepository,
    InMemoryMessageStore,
    InMemoryModelBindingStore,
    InMemoryModelCatalog,
    InMemoryOwnerBootstrap,
    InMemoryProfileDefaultsSeeder,
    InMemoryProfileRepository,
    InMemoryRunStore,
    InMemorySessionStore,
    InMemoryUserRepository,
)
from newray.bootstrap.api import create_app
from newray.modules.conversations import ConversationService
from newray.modules.identity import IdentityService
from newray.modules.models import RUNTIME_OLLAMA, ModelInfo, ModelStatus
from newray.modules.models.adapters.echo import EchoChatModel
from newray.modules.profiles import ProfileService
from newray.modules.runs import RunState, ToolInvocationState

MODEL = ModelInfo(
    name="llama3.1",
    runtime=RUNTIME_OLLAMA,
    digest="sha256:abc123",
    status=ModelStatus.QUALIFIED,
    capabilities=("chat",),
)
RESOURCE = "gpu:0"


def _profiles() -> ProfileService:
    catalog = InMemoryModelCatalog((MODEL,))
    profiles_repo = InMemoryProfileRepository()
    bindings_store = InMemoryModelBindingStore()
    return ProfileService(
        profiles_repo,
        bindings_store,
        catalog,
        FakeClock(datetime(2026, 9, 24, tzinfo=UTC)),
        InMemoryProfileDefaultsSeeder(profiles_repo, bindings_store),
        default_model_name="llama3.1",
    )


@pytest.fixture()
def service() -> IdentityService:
    users = InMemoryUserRepository()
    sessions = InMemorySessionStore()
    return IdentityService(
        users,
        sessions,
        FakeClock(datetime(2026, 9, 24, tzinfo=UTC)),
        InMemoryOwnerBootstrap(users, sessions),
    )


@pytest.fixture()
def conversations() -> ConversationService:
    return ConversationService(
        InMemoryConversationRepository(),
        InMemoryMessageStore(),
        FakeClock(datetime(2026, 9, 24, tzinfo=UTC)),
    )


def _client(
    service: IdentityService, conversations: ConversationService, store: InMemoryRunStore
) -> TestClient:
    return TestClient(
        create_app(
            service,
            conversations,
            _profiles(),
            cookie_secure=False,
            chat_model=EchoChatModel(),
            run_store=store,
            run_events_poll_seconds=0.01,
        ),
        headers={"Origin": "http://testserver"},
    )


def _bootstrap(client: TestClient) -> None:
    assert (
        client.post(
            "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
        ).status_code
        == 201
    )


def _create_conversation(client: TestClient) -> str:
    response = client.post("/api/v1/conversations", json={"title": "Lavoro"})
    assert response.status_code == 201
    return response.json()["id"]


def _default_profile_id(client: TestClient) -> str:
    assert client.post("/api/v1/profiles/defaults").status_code == 200
    return client.get("/api/v1/profiles").json()["items"][0]["id"]


def _parse_sse(body: str) -> list[tuple[str | None, int | None, str]]:
    """Frame grezzi ``(event, id, data)`` in ordine, senza assumere che
    ``id:`` sia sempre presente (a differenza dell'helper di test_http_chat_run)."""
    frames: list[tuple[str | None, int | None, str]] = []
    event: str | None = None
    event_id: int | None = None
    data_lines: list[str] = []
    for line in body.split("\n"):
        if line.startswith("id: "):
            event_id = int(line[len("id: ") :])
        elif line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            data_lines.append(line[len("data: ") :])
        elif line == "" and event is not None:
            frames.append((event, event_id, "".join(data_lines)))
            event, event_id, data_lines = None, None, []
    return frames


def _create_run(client: TestClient, conversation_id: str, profile_id: str, key: str) -> str:
    created = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        json={"profile_id": profile_id, "content": "Ciao", "idempotency_key": key},
    )
    assert created.status_code == 201
    return created.json()["id"]


def test_replay_completo_di_un_run_gia_terminale(
    service: IdentityService, conversations: ConversationService
) -> None:
    store = InMemoryRunStore()
    client = _client(service, conversations, store)
    _bootstrap(client)
    conversation_id = _create_conversation(client)
    profile_id = _default_profile_id(client)
    run_id = _create_run(client, conversation_id, profile_id, "run-1")

    worker_id = uuid.uuid4()
    claimed = store.claim(worker_id, 30, RESOURCE)
    assert claimed is not None
    store.heartbeat(claimed.id, worker_id, claimed.fence, 30, "Ciao mondo", RESOURCE)
    store.finalize(
        claimed.id,
        worker_id,
        claimed.fence,
        RunState.COMPLETED,
        "stop",
        5,
        2,
        1_000_000,
        "Ciao mondo",
        RESOURCE,
    )

    response = client.get(f"/api/v1/runs/{run_id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _parse_sse(response.text)
    assert [f[0] for f in frames] == [
        "run.queued",
        "run.started",
        "message.delta",
        "run.completed",
    ]
    assert [f[1] for f in frames] == [1, 2, 3, 4]
    assert json.loads(frames[-1][2]) == {
        "finish_reason": "stop",
        "text": "Ciao mondo",
        "prompt_tokens": 5,
        "completion_tokens": 2,
        "eval_duration_ns": 1_000_000,
    }


def test_replay_parziale_da_after_sequence(
    service: IdentityService, conversations: ConversationService
) -> None:
    store = InMemoryRunStore()
    client = _client(service, conversations, store)
    _bootstrap(client)
    conversation_id = _create_conversation(client)
    profile_id = _default_profile_id(client)
    run_id = _create_run(client, conversation_id, profile_id, "run-1")

    worker_id = uuid.uuid4()
    claimed = store.claim(worker_id, 30, RESOURCE)
    assert claimed is not None
    store.finalize(
        claimed.id, worker_id, claimed.fence, RunState.COMPLETED, "stop", 1, 1, 1, "ok", RESOURCE
    )

    response = client.get(f"/api/v1/runs/{run_id}/events", params={"after_sequence": 2})
    frames = _parse_sse(response.text)
    assert [f[0] for f in frames] == ["run.completed"]
    assert frames[0][1] == 3


def test_cursore_scaduto_risponde_con_resync(
    service: IdentityService, conversations: ConversationService
) -> None:

    store = InMemoryRunStore()
    client = _client(service, conversations, store)
    _bootstrap(client)
    conversation_id = _create_conversation(client)
    profile_id = _default_profile_id(client)
    run_id = _create_run(client, conversation_id, profile_id, "run-1")

    worker_id = uuid.uuid4()
    claimed = store.claim(worker_id, 30, RESOURCE)
    assert claimed is not None
    for i in range(60):
        assert store.heartbeat(claimed.id, worker_id, claimed.fence, 30, f"t{i}", RESOURCE)
    store.finalize(
        claimed.id,
        worker_id,
        claimed.fence,
        RunState.COMPLETED,
        "stop",
        1,
        1,
        1,
        "finale",
        RESOURCE,
    )

    # sequence=5 era in origine un message.delta, ora potato (restano solo
    # gli ultimi 50): il primo evento ancora disponibile dopo di esso non è
    # il suo diretto successore → buco, non un replay silenzioso.
    response = client.get(f"/api/v1/runs/{run_id}/events", params={"after_sequence": 5})
    frames = _parse_sse(response.text)
    assert len(frames) == 1
    assert frames[0][0] == "resync"

    payload = json.loads(frames[0][2])
    assert payload["state"] == "completed"
    assert payload["partial_text"] == "finale"


def test_eventi_run_inesistente_404(
    service: IdentityService, conversations: ConversationService
) -> None:
    client = _client(service, conversations, InMemoryRunStore())
    _bootstrap(client)
    response = client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000/events")
    assert response.status_code == 404


def test_cancel_queued_200_e_idempotente(
    service: IdentityService, conversations: ConversationService
) -> None:
    store = InMemoryRunStore()
    client = _client(service, conversations, store)
    _bootstrap(client)
    conversation_id = _create_conversation(client)
    profile_id = _default_profile_id(client)
    run_id = _create_run(client, conversation_id, profile_id, "run-1")

    first = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert first.status_code == 200
    assert first.json()["state"] == "cancelled"
    assert first.json()["finish_reason"] == "cancelled_by_user"

    second = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert second.status_code == 200
    assert second.json() == first.json()


def test_cancel_su_run_terminale_non_cambia_stato(
    service: IdentityService, conversations: ConversationService
) -> None:

    store = InMemoryRunStore()
    client = _client(service, conversations, store)
    _bootstrap(client)
    conversation_id = _create_conversation(client)
    profile_id = _default_profile_id(client)
    run_id = _create_run(client, conversation_id, profile_id, "run-1")

    worker_id = uuid.uuid4()
    claimed = store.claim(worker_id, 30, RESOURCE)
    assert claimed is not None
    store.finalize(
        claimed.id, worker_id, claimed.fence, RunState.COMPLETED, "stop", 1, 1, 1, "ok", RESOURCE
    )

    response = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert response.status_code == 200
    assert response.json()["state"] == "completed"


def test_cancel_run_inesistente_404(
    service: IdentityService, conversations: ConversationService
) -> None:
    client = _client(service, conversations, InMemoryRunStore())
    _bootstrap(client)
    response = client.post("/api/v1/runs/00000000-0000-0000-0000-000000000000/cancel")
    assert response.status_code == 404


def test_active_run_nessun_run_risponde_null(
    service: IdentityService, conversations: ConversationService
) -> None:
    store = InMemoryRunStore()
    client = _client(service, conversations, store)
    _bootstrap(client)
    conversation_id = _create_conversation(client)

    response = client.get(f"/api/v1/conversations/{conversation_id}/active-run")
    assert response.status_code == 200
    assert response.json() is None


def test_active_run_trova_il_run_in_coda_o_in_esecuzione(
    service: IdentityService, conversations: ConversationService
) -> None:
    store = InMemoryRunStore()
    client = _client(service, conversations, store)
    _bootstrap(client)
    conversation_id = _create_conversation(client)
    profile_id = _default_profile_id(client)
    run_id = _create_run(client, conversation_id, profile_id, "run-1")

    queued = client.get(f"/api/v1/conversations/{conversation_id}/active-run")
    assert queued.status_code == 200
    assert queued.json()["id"] == run_id
    assert queued.json()["state"] == "queued"

    worker_id = uuid.uuid4()
    claimed = store.claim(worker_id, 30, RESOURCE)
    assert claimed is not None

    running = client.get(f"/api/v1/conversations/{conversation_id}/active-run")
    assert running.status_code == 200
    assert running.json()["id"] == run_id
    assert running.json()["state"] == "running"


def test_active_run_terminale_non_e_piu_riprendibile(
    service: IdentityService, conversations: ConversationService
) -> None:
    store = InMemoryRunStore()
    client = _client(service, conversations, store)
    _bootstrap(client)
    conversation_id = _create_conversation(client)
    profile_id = _default_profile_id(client)
    _create_run(client, conversation_id, profile_id, "run-1")

    worker_id = uuid.uuid4()
    claimed = store.claim(worker_id, 30, RESOURCE)
    assert claimed is not None
    store.finalize(
        claimed.id, worker_id, claimed.fence, RunState.COMPLETED, "stop", 1, 1, 1, "ok", RESOURCE
    )

    response = client.get(f"/api/v1/conversations/{conversation_id}/active-run")
    assert response.status_code == 200
    assert response.json() is None


def test_active_run_conversazione_fuori_scope_risponde_null_non_404(
    service: IdentityService, conversations: ConversationService
) -> None:
    store = InMemoryRunStore()
    client = _client(service, conversations, store)
    _bootstrap(client)

    response = client.get("/api/v1/conversations/00000000-0000-0000-0000-000000000000/active-run")
    assert response.status_code == 200
    assert response.json() is None


def _record_tool(store, run_id, worker_id, fence):
    """Registra una invocazione tool completa (executing → succeeded)."""
    invocation_id = uuid.uuid4()
    store.record_tool_event(
        run_id,
        worker_id,
        fence,
        invocation_id,
        "call-1",
        "run.status",
        {"run_id": str(run_id)},
        ToolInvocationState.EXECUTING,
        "tool.executing",
        None,
        None,
    )
    store.record_tool_event(
        run_id,
        worker_id,
        fence,
        invocation_id,
        "call-1",
        "run.status",
        {"run_id": str(run_id)},
        ToolInvocationState.SUCCEEDED,
        "tool.succeeded",
        '{"found": true}',
        None,
    )


def test_lista_tool_di_un_run(service: IdentityService, conversations: ConversationService) -> None:
    store = InMemoryRunStore()
    client = _client(service, conversations, store)
    _bootstrap(client)
    conversation_id = _create_conversation(client)
    profile_id = _default_profile_id(client)
    run_id = _create_run(client, conversation_id, profile_id, "run-1")

    worker_id = uuid.uuid4()
    claimed = store.claim(worker_id, 30, RESOURCE)
    assert claimed is not None
    _record_tool(store, claimed.id, worker_id, claimed.fence)

    response = client.get(f"/api/v1/runs/{run_id}/tools")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["tool_name"] == "run.status"
    assert items[0]["state"] == "succeeded"
    assert items[0]["result"] == '{"found": true}'
    assert items[0]["error_code"] is None


def test_lista_tool_run_inesistente_404(
    service: IdentityService, conversations: ConversationService
) -> None:
    client = _client(service, conversations, InMemoryRunStore())
    _bootstrap(client)
    response = client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000/tools")
    assert response.status_code == 404


def test_lista_tool_vuota_se_nessun_tool(
    service: IdentityService, conversations: ConversationService
) -> None:
    store = InMemoryRunStore()
    client = _client(service, conversations, store)
    _bootstrap(client)
    conversation_id = _create_conversation(client)
    profile_id = _default_profile_id(client)
    run_id = _create_run(client, conversation_id, profile_id, "run-1")

    response = client.get(f"/api/v1/runs/{run_id}/tools")
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_eventi_tool_nello_stream(
    service: IdentityService, conversations: ConversationService
) -> None:
    store = InMemoryRunStore()
    client = _client(service, conversations, store)
    _bootstrap(client)
    conversation_id = _create_conversation(client)
    profile_id = _default_profile_id(client)
    run_id = _create_run(client, conversation_id, profile_id, "run-1")

    worker_id = uuid.uuid4()
    claimed = store.claim(worker_id, 30, RESOURCE)
    assert claimed is not None
    _record_tool(store, claimed.id, worker_id, claimed.fence)
    store.finalize(
        claimed.id, worker_id, claimed.fence, RunState.COMPLETED, "stop", 1, 1, 1, "ok", RESOURCE
    )

    response = client.get(f"/api/v1/runs/{run_id}/events")
    frames = _parse_sse(response.text)
    types = [f[0] for f in frames]
    assert "tool.executing" in types
    assert "tool.succeeded" in types
    # I payload tool portano call_id/tool_name/is_error per la UI.
    executing = next(f for f in frames if f[0] == "tool.executing")
    payload = json.loads(executing[2])
    assert payload["tool_name"] == "run.status"
    assert payload["is_error"] is False
