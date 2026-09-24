"""Contratto HTTP della generazione chat (B-09.2; NewRay.md §8.1).

Percorso inline senza worker: il run è sincrono nella richiesta e produce
SSE. I modelli sono deterministici (``EchoChatModel`` e finte in-memory):
nessuna inferenza reale, nessuna rete, nessun runtime Ollama
(NewRay.md §22.1). La prova con Ollama qualificato è B-09.3.

Copre il contratto autorevole di token/s (B-03.2-34) a livello HTTP:
``tokens_per_second`` nel terminale ``done`` deriva da conteggio e durata
del runtime, e resta ``null`` quando il runtime non li fornisce.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
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
    InMemorySessionStore,
    InMemoryUserRepository,
)
from newray.bootstrap.api import create_app
from newray.kernel.errors import InferenceFailed, InferenceTimeout
from newray.modules.conversations import ConversationService
from newray.modules.identity import IdentityService
from newray.modules.models import (
    RUNTIME_OLLAMA,
    ChatRequest,
    Completion,
    ContentDelta,
    ModelInfo,
    ModelStatus,
    StreamEvent,
)
from newray.modules.models.adapters.echo import EchoChatModel
from newray.modules.profiles import ProfileService

MODEL = ModelInfo(
    name="llama3.1",
    runtime=RUNTIME_OLLAMA,
    digest="sha256:abc123",
    status=ModelStatus.QUALIFIED,
    capabilities=("chat",),
)


def _profiles(catalog: InMemoryModelCatalog) -> ProfileService:
    profiles_repo = InMemoryProfileRepository()
    bindings_store = InMemoryModelBindingStore()
    return ProfileService(
        profiles_repo,
        bindings_store,
        catalog,
        FakeClock(datetime(2026, 9, 16, tzinfo=UTC)),
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
        FakeClock(datetime(2026, 9, 16, tzinfo=UTC)),
        InMemoryOwnerBootstrap(users, sessions),
    )


@pytest.fixture()
def conversations() -> ConversationService:
    return ConversationService(
        InMemoryConversationRepository(),
        InMemoryMessageStore(),
        FakeClock(datetime(2026, 9, 16, tzinfo=UTC)),
    )


@pytest.fixture()
def client(service: IdentityService, conversations: ConversationService) -> TestClient:
    """App con catalogo vuoto: il modello di default non è disponibile."""
    return TestClient(
        create_app(
            service,
            conversations,
            _profiles(InMemoryModelCatalog()),
            cookie_secure=False,
            chat_model=EchoChatModel(),
        ),
        headers={"Origin": "http://testserver"},
    )


@pytest.fixture()
def client_with_model(service: IdentityService, conversations: ConversationService) -> TestClient:
    """App con il modello di default presente nel catalogo."""
    return TestClient(
        create_app(
            service,
            conversations,
            _profiles(InMemoryModelCatalog((MODEL,))),
            cookie_secure=False,
            chat_model=EchoChatModel(),
        ),
        headers={"Origin": "http://testserver"},
    )


def _bootstrap(client: TestClient) -> None:
    response = client.post(
        "/api/v1/session", json={"display_name": "Ada", "credential": "test-passphrase-1234"}
    )
    assert response.status_code == 201


def _create_conversation(client: TestClient, title: str = "Lavoro") -> dict:
    response = client.post("/api/v1/conversations", json={"title": title})
    assert response.status_code == 201
    return response.json()


def _default_profile_id(client: TestClient) -> str:
    assert client.post("/api/v1/profiles/defaults").status_code == 200
    response = client.get("/api/v1/profiles")
    assert response.status_code == 200
    return response.json()["items"][0]["id"]


def _run(
    client: TestClient,
    conversation_id: str,
    content: str,
    profile_id: str,
    *,
    idempotency_key: str | None = None,
) -> TestClient:
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    return client.stream(
        "POST",
        f"/api/v1/conversations/{conversation_id}/run",
        json={"content": content, "profile_id": profile_id},
        headers=headers,
    )


def _parse_sse(response: TestClient) -> list[tuple[str, dict]]:
    """Parsa gli eventi SSE in (nome, payload)."""
    events: list[tuple[str, dict]] = []
    name: str | None = None
    data_lines: list[str] = []
    for line in response.iter_lines():
        if line.startswith("event: "):
            name = line[len("event: ") :]
        elif line.startswith("data: "):
            data_lines.append(line[len("data: ") :])
        elif line == "" and name is not None:
            events.append((name, json.loads("".join(data_lines))))
            name = None
            data_lines = []
    return events


class FailingChatModel:
    """Modello che fallisce durante lo stream con un guasto di dominio."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        async def generate() -> AsyncIterator[StreamEvent]:
            yield ContentDelta(text="parziale")
            raise self._error

        return generate()


class MetricChatModel:
    """Modello deterministico che dichiara conteggio e durata del runtime."""

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        async def generate() -> AsyncIterator[StreamEvent]:
            yield ContentDelta(text="ok")
            yield Completion(
                finish_reason="stop",
                prompt_tokens=5,
                completion_tokens=20,
                eval_duration_ns=2_000_000_000,
            )

        return generate()


class EmptyStreamModel:
    """Modello che non produce né delta né terminale: stream vuoto."""

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        async def generate() -> AsyncIterator[StreamEvent]:
            return
            yield  # pragma: no cover

        return generate()


def _client_with_model(
    service: IdentityService,
    conversations: ConversationService,
    chat_model: object,
    catalog: InMemoryModelCatalog,
) -> TestClient:
    return TestClient(
        create_app(
            service,
            conversations,
            _profiles(catalog),
            cookie_secure=False,
            chat_model=chat_model,
        ),
        headers={"Origin": "http://testserver"},
    )


def test_run_richiede_autenticazione(client: TestClient) -> None:
    response = client.post(
        f"/api/v1/conversations/{uuid.uuid4()}/run",
        json={"content": "ciao", "profile_id": str(uuid.uuid4())},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_run_conversazione_assente_è_404(client_with_model: TestClient) -> None:
    _bootstrap(client_with_model)
    response = client_with_model.post(
        f"/api/v1/conversations/{uuid.uuid4()}/run",
        json={"content": "ciao", "profile_id": _default_profile_id(client_with_model)},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_run_profilo_sconosciuto_è_404(client_with_model: TestClient) -> None:
    _bootstrap(client_with_model)
    conversation = _create_conversation(client_with_model)
    response = client_with_model.post(
        f"/api/v1/conversations/{conversation['id']}/run",
        json={"content": "ciao", "profile_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_run_modello_mancente_è_503_model_unavailable(client: TestClient) -> None:
    """Modello assente dal catalogo: errore esplicito recuperabile, mai un
    fallback invisibile (ADR 0003)."""
    _bootstrap(client)
    conversation = _create_conversation(client)
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/run",
        json={"content": "ciao", "profile_id": _default_profile_id(client)},
    )
    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"code", "message", "retryable", "correlation_id"}
    assert body["code"] == "MODEL_UNAVAILABLE"
    # Recuperabile con un'azione esplicita, non con un retry cieco.
    assert body["retryable"] is False
    # Validazione prima della scrittura: nessun prompt orfano.
    messages = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
    assert messages == []


def test_run_valido_produce_sse_e_persiste_messaggi(client_with_model: TestClient) -> None:
    """Flusso completo: binding → contesto → streaming → persistenza."""
    _bootstrap(client_with_model)
    conversation = _create_conversation(client_with_model)
    profile_id = _default_profile_id(client_with_model)

    with _run(client_with_model, conversation["id"], "ciao", profile_id) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-newray-run-mode"] == "inline-preview"
        events = _parse_sse(response)

    names = [name for name, _ in events]
    assert "error" not in names
    assert names[-1] == "done"
    deltas = [data["text"] for name, data in events if name == "delta"]
    assert "".join(deltas) == "Echo: ciao"

    done = events[-1][1]
    assert set(done) == {
        "finish_reason",
        "message_id",
        "user_message_id",
        "prompt_tokens",
        "completion_tokens",
        "eval_duration_ns",
        "tokens_per_second",
        "model",
        "digest",
        "context_message_count",
        "context_character_count",
        "context_truncated",
        "max_output_tokens",
        "replayed",
        "correlation_id",
    }
    assert done["finish_reason"] == "stop"
    assert done["model"] == "llama3.1"
    assert done["digest"] == "sha256:abc123"
    assert isinstance(done["correlation_id"], str)
    assert done["correlation_id"]
    # EchoChatModel non dichiara la durata del runtime: token/s resta null.
    assert done["tokens_per_second"] is None
    assert done["eval_duration_ns"] is None
    assert done["max_output_tokens"] == 2048
    assert done["context_truncated"] is False
    assert done["replayed"] is False
    assert isinstance(done["prompt_tokens"], int)
    assert isinstance(done["completion_tokens"], int)
    assert done["completion_tokens"] > 0
    assert done["message_id"]
    assert done["user_message_id"]

    # Persistenza: user (sequenza 1) e assistant (sequenza 2).
    messages = client_with_model.get(f"/api/v1/conversations/{conversation['id']}/messages").json()[
        "items"
    ]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "ciao"
    assert messages[0]["sequence"] == 1
    assert messages[1]["content"] == "Echo: ciao"
    assert messages[1]["sequence"] == 2
    assert str(messages[0]["id"]) == done["user_message_id"]
    assert str(messages[1]["id"]) == done["message_id"]


def test_run_conteggio_e_durata_producono_tokens_per_second(
    service: IdentityService, conversations: ConversationService
) -> None:
    """B-03.2-34: il terminale done espone token/s calcolato dal runtime."""
    _bootstrap(
        client := _client_with_model(
            service, conversations, MetricChatModel(), InMemoryModelCatalog((MODEL,))
        )
    )
    conversation = _create_conversation(client)
    with _run(client, conversation["id"], "ciao", _default_profile_id(client)) as response:
        events = _parse_sse(response)

    done = events[-1][1]
    assert events[-1][0] == "done"
    assert done["prompt_tokens"] == 5
    assert done["completion_tokens"] == 20
    assert done["eval_duration_ns"] == 2_000_000_000
    # 20 token / 2 s = 10.0 token/s, arrotondato a due decimali.
    assert done["tokens_per_second"] == 10.0


def test_run_guasto_di_inferenza_è_evento_sse_senza_done(
    service: IdentityService, conversations: ConversationService
) -> None:
    """Guasto durante lo stream: errore esplicito, nessun done (mai una
    false completion), nessun messaggio dell'assistente persistito."""
    _bootstrap(
        client := _client_with_model(
            service,
            conversations,
            FailingChatModel(InferenceFailed("guasto simulato del runtime")),
            InMemoryModelCatalog((MODEL,)),
        )
    )
    conversation = _create_conversation(client)
    with _run(client, conversation["id"], "ciao", _default_profile_id(client)) as response:
        events = _parse_sse(response)

    names = [name for name, _ in events]
    assert "done" not in names
    error = next(data for name, data in events if name == "error")
    assert error["code"] == "INFERENCE_FAILED"

    messages = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
    assert messages == []


def test_run_timeout_di_inferenza_è_evento_sse(
    service: IdentityService, conversations: ConversationService
) -> None:
    _bootstrap(
        client := _client_with_model(
            service,
            conversations,
            FailingChatModel(InferenceTimeout("timeout simulato del runtime")),
            InMemoryModelCatalog((MODEL,)),
        )
    )
    conversation = _create_conversation(client)
    with _run(client, conversation["id"], "ciao", _default_profile_id(client)) as response:
        events = _parse_sse(response)

    error = next(data for name, data in events if name == "error")
    assert error["code"] == "INFERENCE_TIMEOUT"
    assert "done" not in [name for name, _ in events]


def test_run_stream_vuoto_è_errore_senza_prompt_orfano(
    service: IdentityService, conversations: ConversationService
) -> None:
    """Uno stream senza terminale non viene presentato come completato."""
    _bootstrap(
        client := _client_with_model(
            service, conversations, EmptyStreamModel(), InMemoryModelCatalog((MODEL,))
        )
    )
    conversation = _create_conversation(client)
    with _run(client, conversation["id"], "ciao", _default_profile_id(client)) as response:
        events = _parse_sse(response)

    assert [name for name, _ in events] == ["error"]
    assert events[0][1]["code"] == "INFERENCE_FAILED"

    messages = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
    assert messages == []


def test_run_idempotente_riproduce_la_risposta_senza_duplicare(
    service: IdentityService, conversations: ConversationService
) -> None:
    client = _client_with_model(
        service, conversations, MetricChatModel(), InMemoryModelCatalog((MODEL,))
    )
    _bootstrap(client)
    conversation = _create_conversation(client)
    profile_id = _default_profile_id(client)

    with _run(
        client,
        conversation["id"],
        "ciao",
        profile_id,
        idempotency_key="http-request-1",
    ) as response:
        first = _parse_sse(response)
    with _run(
        client,
        conversation["id"],
        "ciao",
        profile_id,
        idempotency_key="http-request-1",
    ) as response:
        replay = _parse_sse(response)

    assert first[-1][1]["replayed"] is False
    assert replay[-1][1]["replayed"] is True
    assert replay[-1][1]["finish_reason"] == "replayed"
    assert first[-1][1]["message_id"] == replay[-1][1]["message_id"]
    messages = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
    assert [message["role"] for message in messages] == ["user", "assistant"]


def test_run_contenuto_vuoto_è_422(client_with_model: TestClient) -> None:
    _bootstrap(client_with_model)
    conversation = _create_conversation(client_with_model)
    response = client_with_model.post(
        f"/api/v1/conversations/{conversation['id']}/run",
        json={"content": "", "profile_id": _default_profile_id(client_with_model)},
    )
    assert response.status_code == 422


def test_run_profile_id_non_uuid_è_422(client_with_model: TestClient) -> None:
    _bootstrap(client_with_model)
    conversation = _create_conversation(client_with_model)
    response = client_with_model.post(
        f"/api/v1/conversations/{conversation['id']}/run",
        json={"content": "ciao", "profile_id": "non-un-uuid"},
    )
    assert response.status_code == 422
