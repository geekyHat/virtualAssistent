"""Contratto dell'adapter Ollama: catalogo e chat in streaming (B-03).

Senza rete: ``httpx.AsyncClient`` è sostenuto da un trasporto in memoria
controllato (NewRay.md §22.1). Il fake verifica il contratto — stato
onesto del catalogo, eventi di stream, limiti espliciti, errori tipizzati,
cancellazione — non la qualità live dei modelli (quella è opt-in,
``tests/live``). I guasti vendor restano nel trasporto: sui codici stabili
di dominio (NewRay.md §6.1) li mappa l'adapter.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest

from newray.infrastructure.network import HttpClient
from newray.kernel.errors import InferenceFailed, InferenceTimeout, ModelUnavailable
from newray.modules.models import (
    RUNTIME_OLLAMA,
    ChatMessage,
    ChatRequest,
    ChatRole,
    Completion,
    ContentDelta,
    ModelInfo,
    ModelStatus,
    ReadinessState,
)
from newray.modules.models.adapters.empty import EmptyModelCatalog
from newray.modules.models.adapters.ollama import OllamaChatModel, OllamaModelCatalog

CHUNK_A = json.dumps({"message": {"role": "assistant", "content": "Ciao"}, "done": False})
CHUNK_B = json.dumps({"message": {"role": "assistant", "content": " mondo"}, "done": False})
CHUNK_EMPTY = json.dumps({"message": {"role": "assistant", "content": ""}, "done": False})
DONE = json.dumps(
    {
        "message": {"role": "assistant", "content": ""},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 9,
        "eval_count": 4,
        "eval_duration": 2_000_000_000,
    }
)


class _LinesStream(httpx.AsyncByteStream):
    """Corpo NDJSON emesso riga per riga, con conteggio del consumo.

    ``fail_after`` simula un guasto di lettura al ``fail_after``-esimo
    chunk (timeout di inattività a metà stream).
    """

    def __init__(
        self,
        lines: list[str],
        fail_after: int | None = None,
        fail_with: type[httpx.HTTPError] = httpx.ReadTimeout,
    ) -> None:
        self._lines = lines
        self._fail_after = fail_after
        self._fail_with = fail_with
        self.read = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for index, line in enumerate(self._lines):
            self.read += 1
            if self._fail_after is not None and index == self._fail_after:
                raise self._fail_with("guasto simulato a metà stream")
            yield line.encode("utf-8") + b"\n"

    async def aclose(self) -> None:
        return None


class _NeverEndingStream(httpx.AsyncByteStream):
    """Server che produce un chunk e poi resta aperto finché non è annullato."""

    def __init__(self) -> None:
        self.closed = asyncio.Event()
        self.started = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.started.set()
        yield CHUNK_A.encode("utf-8") + b"\n"
        await self.closed.wait()

    async def aclose(self) -> None:
        self.closed.set()


class _Transport(httpx.AsyncBaseTransport):
    """Trasporto in memoria: risposta fissa, guasto di connessione, o
    stream controllato. Registra le richieste per ispezionare i payload."""

    def __init__(
        self,
        response: httpx.Response | None = None,
        connect_error: type[httpx.HTTPError] | None = None,
        stream: httpx.AsyncByteStream | None = None,
    ) -> None:
        self._response = response
        self._connect_error = connect_error
        self._stream = stream
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._connect_error is not None:
            raise self._connect_error("servizio irraggiungibile (simulato)")
        if self._stream is not None:
            return httpx.Response(200, stream=self._stream)
        assert self._response is not None
        return self._response


def _client(
    transport: httpx.AsyncBaseTransport,
    timeout: httpx.Timeout | None = None,
) -> HttpClient:
    inner = httpx.AsyncClient(
        transport=transport,
        base_url="http://ollama.test",
        timeout=timeout or httpx.Timeout(connect=3, read=7, write=11, pool=13),
    )
    return HttpClient("http://ollama.test", client=inner)


def _request(
    model: str = "llama3.1",
    max_tokens: int | None = None,
    parameters: dict[str, object] | None = None,
    inactivity_timeout: timedelta | None = None,
) -> ChatRequest:
    return ChatRequest(
        model=model,
        runtime=RUNTIME_OLLAMA,
        messages=(ChatMessage(role=ChatRole.USER, content="Ciao"),),
        parameters=parameters if parameters is not None else {},
        max_tokens=max_tokens,
        inactivity_timeout=inactivity_timeout,
    )


def _json_response(payload: object, status: int = 200) -> httpx.Response:
    if isinstance(payload, (dict, list)):
        return httpx.Response(status, json=payload)
    return httpx.Response(status, content=payload.encode("utf-8"))


# --- Catalogo -------------------------------------------------------------


def test_catalogo_elenca_modelli_installati() -> None:
    payload = {
        "models": [
            {"name": "llama3.1", "digest": "sha256:abc123"},
            {"name": "mistral:7b"},
            "elemento non valido",
            {"size": 123},
            {"name": ""},
        ]
    }
    transport = _Transport(_json_response(payload))
    catalog = OllamaModelCatalog(_client(transport))

    models = asyncio.run(catalog.list_models())

    assert [transport.requests[0].url.path] == ["/api/tags"]
    assert models == [
        ModelInfo(
            name="llama3.1",
            runtime=RUNTIME_OLLAMA,
            digest="sha256:abc123",
            status=ModelStatus.INSTALLED,
            capabilities=(),
        ),
        ModelInfo(
            name="mistral:7b",
            runtime=RUNTIME_OLLAMA,
            digest=None,
            status=ModelStatus.INSTALLED,
            capabilities=(),
        ),
    ]
    # Stato onesto: installed dal runtime; compatible/qualified richiedono
    # prove registrate (§9.1) e qui non compaiono.


def test_catalogo_irraggiungibile_è_vuoto() -> None:
    transport = _Transport(connect_error=httpx.ConnectError)
    catalog = OllamaModelCatalog(_client(transport))
    assert asyncio.run(catalog.list_models()) == []


def test_catalogo_timeout_è_tipizzato() -> None:
    transport = _Transport(connect_error=httpx.ConnectTimeout)
    catalog = OllamaModelCatalog(_client(transport))
    with pytest.raises(InferenceTimeout) as excinfo:
        asyncio.run(catalog.list_models())
    assert excinfo.value.code == "INFERENCE_TIMEOUT"


def test_catalogo_errore_http_è_tipizzato() -> None:
    transport = _Transport(_json_response({"error": "boom"}, status=500))
    catalog = OllamaModelCatalog(_client(transport))
    with pytest.raises(InferenceFailed) as excinfo:
        asyncio.run(catalog.list_models())
    assert excinfo.value.code == "INFERENCE_FAILED"


def test_catalogo_corpo_non_json_è_tipizzato() -> None:
    transport = _Transport(_json_response("<html>gateway</html>"))
    catalog = OllamaModelCatalog(_client(transport))
    with pytest.raises(InferenceFailed):
        asyncio.run(catalog.list_models())


def test_catalogo_payload_senza_modelli_è_tipizzato() -> None:
    transport = _Transport(_json_response({"models": "assente"}))
    catalog = OllamaModelCatalog(_client(transport))
    with pytest.raises(InferenceFailed):
        asyncio.run(catalog.list_models())


@pytest.mark.parametrize(
    ("tags", "shown", "expected"),
    [
        ({"models": []}, None, ReadinessState.CATALOG_EMPTY),
        ({"models": [{"name": "altro"}]}, None, ReadinessState.MODEL_MISSING),
        (
            {"models": [{"name": "pilot", "digest": "sha256:abc"}]},
            {"capabilities": ["completion", "tools"]},
            ReadinessState.INSTALLED_UNVERIFIED,
        ),
        (
            {"models": [{"name": "pilot", "digest": "sha256:abc"}]},
            {"capabilities": []},
            ReadinessState.INSTALLED_UNVERIFIED,
        ),
        (
            {"models": [{"name": "pilot", "digest": "sha256:abc"}]},
            {"capabilities": ["embedding"]},
            ReadinessState.ARTIFACT_INCOMPATIBLE,
        ),
        (
            {"models": [{"name": "pilot", "digest": "sha256:abc"}]},
            {"capabilities": "completion"},
            ReadinessState.RUNTIME_ERROR,
        ),
        ({"models": "bad"}, None, ReadinessState.RUNTIME_ERROR),
    ],
)
def test_readiness_distingue_stati_senza_qualificare_capacita(
    tags: object, shown: object | None, expected: ReadinessState
) -> None:
    paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags)
        assert request.url.path == "/api/show"
        assert json.loads(request.content) == {"model": "pilot"}
        return httpx.Response(200, json=shown)

    transport = httpx.MockTransport(respond)
    catalog = OllamaModelCatalog(_client(transport))
    observed = asyncio.run(catalog.readiness("pilot"))
    assert observed.state is expected
    assert paths == (["/api/tags", "/api/show"] if shown is not None else ["/api/tags"])
    if expected is ReadinessState.INSTALLED_UNVERIFIED:
        assert isinstance(shown, dict)
        assert observed.declared_capabilities == tuple(shown["capabilities"])
    elif expected is ReadinessState.ARTIFACT_INCOMPATIBLE:
        assert observed.declared_capabilities == ("embedding",)
    else:
        assert observed.declared_capabilities == ()


def test_readiness_runtime_irraggiungibile_e_non_configurato() -> None:
    transport = _Transport(connect_error=httpx.ConnectError)
    catalog = OllamaModelCatalog(_client(transport))
    assert asyncio.run(catalog.readiness("pilot")).state is ReadinessState.UNREACHABLE
    assert asyncio.run(catalog.readiness(None)).state is ReadinessState.NOT_CONFIGURED
    assert len(transport.requests) == 1
    assert (
        asyncio.run(EmptyModelCatalog().readiness("pilot")).state is ReadinessState.NOT_CONFIGURED
    )


# --- Chat in streaming ----------------------------------------------------


def test_stream_produce_eventi_da_ndjson() -> None:
    stream = _LinesStream([CHUNK_A, CHUNK_B, CHUNK_EMPTY, DONE])
    model = OllamaChatModel(_client(_Transport(stream=stream)))

    async def scenario() -> list[Any]:
        events: list[Any] = []
        async for event in model.stream(_request()):
            events.append(event)
        return events

    events = asyncio.run(scenario())
    assert events == [
        ContentDelta(text="Ciao"),
        ContentDelta(text=" mondo"),
        Completion(
            finish_reason="stop",
            prompt_tokens=9,
            completion_tokens=4,
            eval_duration_ns=2_000_000_000,
        ),
    ]


def test_payload_contiene_modello_messaggi_e_limiti() -> None:
    stream = _LinesStream([DONE])
    transport = _Transport(stream=stream)
    model = OllamaChatModel(_client(transport))
    request = _request(max_tokens=42, parameters={"temperature": 0.2})

    async def scenario() -> None:
        async for _ in model.stream(request):
            pass

    asyncio.run(scenario())
    payload = json.loads(transport.requests[0].content)
    assert payload["model"] == "llama3.1"
    assert payload["messages"] == [{"role": "user", "content": "Ciao"}]
    assert payload["stream"] is True
    assert payload["think"] is False
    # I limiti della richiesta sono applicati al runtime (B-03).
    assert payload["options"] == {"temperature": 0.2, "num_ctx": 8192, "num_predict": 42}


def test_senza_override_il_contesto_pilot_resta_esplicito() -> None:
    stream = _LinesStream([DONE])
    transport = _Transport(stream=stream)
    model = OllamaChatModel(_client(transport))

    async def scenario() -> None:
        async for _ in model.stream(_request()):
            pass

    asyncio.run(scenario())
    payload = json.loads(transport.requests[0].content)
    assert payload["options"] == {"num_ctx": 8192}


@pytest.mark.parametrize("requested", [True, "16384", 512, 32768])
def test_contesto_fuori_budget_non_arriva_a_ollama(requested: object) -> None:
    transport = _Transport(stream=_LinesStream([DONE]))
    model = OllamaChatModel(_client(transport))

    async def scenario() -> None:
        async for _ in model.stream(_request(parameters={"num_ctx": requested})):
            pass

    with pytest.raises(InferenceFailed):
        asyncio.run(scenario())
    assert transport.requests == []


def test_senza_override_il_client_conserva_tutti_i_timeout() -> None:
    """L'assenza di override eredita i quattro timeout, non li disabilita."""
    stream = _LinesStream([DONE])
    transport = _Transport(stream=stream)
    model = OllamaChatModel(_client(transport))

    async def scenario() -> None:
        request = _request()
        async for _ in model.stream(request):
            pass

    asyncio.run(scenario())
    assert transport.requests[0].extensions["timeout"] == {
        "connect": 3,
        "read": 7,
        "write": 11,
        "pool": 13,
    }


def test_override_di_inattivita_conserva_connect_write_e_pool() -> None:
    stream = _LinesStream([DONE])
    transport = _Transport(stream=stream)
    model = OllamaChatModel(_client(transport))

    async def scenario() -> None:
        async for _ in model.stream(_request(inactivity_timeout=timedelta(seconds=17))):
            pass

    asyncio.run(scenario())
    assert transport.requests[0].extensions["timeout"] == {
        "connect": 3,
        "read": 17,
        "write": 11,
        "pool": 13,
    }


@pytest.mark.parametrize("timeout", [timedelta(0), timedelta(seconds=-1)])
def test_http_client_rifiuta_timeout_non_positivi(timeout: timedelta) -> None:
    with pytest.raises(ValueError, match="deve essere positivo"):
        HttpClient("http://ollama.test", connect_timeout=timeout)


def test_http_client_rifiuta_client_con_timeout_disabilitato() -> None:
    inner = httpx.AsyncClient(
        transport=_Transport(stream=_LinesStream([DONE])),
        base_url="http://ollama.test",
        timeout=httpx.Timeout(None),
    )
    with pytest.raises(ValueError, match="timeout connect"):
        HttpClient("http://ollama.test", client=inner)


def test_modello_assente_è_404() -> None:
    transport = _Transport(_json_response({"error": "not found"}, status=404))
    model = OllamaChatModel(_client(transport))
    with pytest.raises(ModelUnavailable) as excinfo:
        asyncio.run(_drain(model))
    assert excinfo.value.code == "MODEL_UNAVAILABLE"


def test_errore_del_runtime_è_tipizzato() -> None:
    transport = _Transport(_json_response({"error": "boom"}, status=500))
    model = OllamaChatModel(_client(transport))
    with pytest.raises(InferenceFailed) as excinfo:
        asyncio.run(_drain(model))
    assert excinfo.value.code == "INFERENCE_FAILED"


def test_runtime_irraggiungibile_è_recuperabile() -> None:
    transport = _Transport(connect_error=httpx.ConnectError)
    model = OllamaChatModel(_client(transport))
    with pytest.raises(ModelUnavailable) as excinfo:
        asyncio.run(_drain(model))
    assert excinfo.value.code == "MODEL_UNAVAILABLE"


def test_timeout_di_connessione_è_tipizzato() -> None:
    transport = _Transport(connect_error=httpx.ConnectTimeout)
    model = OllamaChatModel(_client(transport))
    with pytest.raises(InferenceTimeout) as excinfo:
        asyncio.run(_drain(model))
    assert excinfo.value.code == "INFERENCE_TIMEOUT"


def test_errore_nel_flusso_è_tipizzato() -> None:
    stream = _LinesStream([CHUNK_A, json.dumps({"error": "modello rotto"})])
    model = OllamaChatModel(_client(_Transport(stream=stream)))
    with pytest.raises(InferenceFailed):
        asyncio.run(_drain(model))


def test_riga_non_valida_è_tipizzata() -> None:
    stream = _LinesStream(["questo non è JSON"])
    model = OllamaChatModel(_client(_Transport(stream=stream)))
    with pytest.raises(InferenceFailed):
        asyncio.run(_drain(model))


def test_timeout_a_metà_stream_è_tipizzato() -> None:
    stream = _LinesStream([CHUNK_A, CHUNK_B], fail_after=1)
    model = OllamaChatModel(_client(_Transport(stream=stream)))
    with pytest.raises(InferenceTimeout) as excinfo:
        asyncio.run(_drain(model))
    assert excinfo.value.code == "INFERENCE_TIMEOUT"


def test_cancellazione_interrompe_il_flusso(monkeypatch: pytest.MonkeyPatch) -> None:
    """Terminare lo stream (``aclose``) interrompe la generazione e
    rilascia la connessione: l'adapter è il punto di cancellazione
    (NewRay.md §6.1, B-03)."""
    stream = _LinesStream([CHUNK_A, CHUNK_B, DONE])
    transport = _Transport(stream=stream)
    closed: list[httpx.Response] = []
    original = httpx.Response.aclose

    async def spy(self: httpx.Response) -> None:
        closed.append(self)
        await original(self)

    monkeypatch.setattr(httpx.Response, "aclose", spy)
    model = OllamaChatModel(_client(transport))

    async def scenario() -> None:
        generator = model.stream(_request())
        first = await generator.__anext__()
        assert isinstance(first, ContentDelta) and first.text == "Ciao"
        await generator.aclose()
        with pytest.raises(StopAsyncIteration):
            await generator.__anext__()

    asyncio.run(scenario())
    # La connessione è stata rilasciata e la generazione non è andata a
    # finire: solo un chunk su tre è stato letto.
    assert len(closed) == 1
    assert stream.read == 1


def test_cancellazione_chiude_anche_uno_stream_senza_fine() -> None:
    """La deadline totale sarà nel run; intanto cancel non resta in attesa.

    Il server può continuare a tenere aperta la risposta pur avendo prodotto
    un chunk. Il consumer che possiede il run deve poter chiudere subito lo
    stream e liberare il socket.
    """
    stream = _NeverEndingStream()
    model = OllamaChatModel(_client(_Transport(stream=stream)))

    async def scenario() -> None:
        generator = model.stream(_request())
        first = await generator.__anext__()
        assert first == ContentDelta(text="Ciao")
        await generator.aclose()
        await asyncio.wait_for(stream.closed.wait(), timeout=0.1)

    asyncio.run(scenario())


def test_conteggio_token_omesso_dal_runtime() -> None:
    done = json.dumps({"done": True, "done_reason": "length"})
    stream = _LinesStream([CHUNK_A, done])
    model = OllamaChatModel(_client(_Transport(stream=stream)))
    events = asyncio.run(_collect(model))
    assert events[-1] == Completion(
        finish_reason="length",
        prompt_tokens=None,
        completion_tokens=None,
        eval_duration_ns=None,
    )


def test_done_reason_del_runtime_non_viene_sostituito() -> None:
    done = json.dumps({"done": True, "done_reason": "length"})
    model = OllamaChatModel(_client(_Transport(stream=_LinesStream([CHUNK_A, done]))))
    assert asyncio.run(_collect(model))[-1] == Completion(
        finish_reason="length",
        prompt_tokens=None,
        completion_tokens=None,
        eval_duration_ns=None,
    )


def test_thinking_del_runtime_non_diventa_contenuto_visibile() -> None:
    """Il contratto corrente invia ``think: false``; una risposta inattesa
    non mescola comunque il ragionamento con il testo della conversazione."""
    chunk = json.dumps(
        {"done": False, "message": {"content": "Risposta", "thinking": "ragionamento"}}
    )
    model = OllamaChatModel(_client(_Transport(stream=_LinesStream([chunk, DONE]))))
    assert asyncio.run(_collect(model))[0] == ContentDelta(text="Risposta")


@pytest.mark.parametrize(
    "line",
    [
        json.dumps({"done": True}),
        json.dumps({"done": True, "done_reason": 4}),
        json.dumps({"done": "true", "done_reason": "stop"}),
        json.dumps({"done": False, "message": {}}),
        json.dumps({"done": False, "message": {"content": 4}}),
    ],
)
def test_chunk_con_schema_invalido_è_tipizzato(line: str) -> None:
    model = OllamaChatModel(_client(_Transport(stream=_LinesStream([line]))))
    with pytest.raises(InferenceFailed):
        asyncio.run(_drain(model))


def test_eof_senza_done_è_tipizzato() -> None:
    model = OllamaChatModel(_client(_Transport(stream=_LinesStream([CHUNK_A]))))
    with pytest.raises(InferenceFailed, match="senza completamento"):
        asyncio.run(_drain(model))


def test_doppio_terminale_è_tipizzato() -> None:
    model = OllamaChatModel(_client(_Transport(stream=_LinesStream([DONE, DONE]))))
    with pytest.raises(InferenceFailed, match="dopo il completamento"):
        asyncio.run(_drain(model))


def test_eval_duration_valido_produce_tokens_per_second() -> None:
    done = json.dumps(
        {
            "done": True,
            "done_reason": "stop",
            "eval_count": 20,
            "eval_duration": 2_000_000_000,
        }
    )
    model = OllamaChatModel(_client(_Transport(stream=_LinesStream([CHUNK_A, done]))))
    completion = asyncio.run(_collect(model))[-1]
    assert isinstance(completion, Completion)
    assert completion.eval_duration_ns == 2_000_000_000
    assert completion.tokens_per_second == 10.0


def test_eval_duration_nullo_produce_tokens_per_second_nullo() -> None:
    done = json.dumps({"done": True, "done_reason": "stop", "eval_count": 20})
    model = OllamaChatModel(_client(_Transport(stream=_LinesStream([CHUNK_A, done]))))
    completion = asyncio.run(_collect(model))[-1]
    assert isinstance(completion, Completion)
    assert completion.eval_duration_ns is None
    assert completion.tokens_per_second is None


def test_eval_duration_zero_produce_tokens_per_second_nullo() -> None:
    done = json.dumps({"done": True, "done_reason": "stop", "eval_count": 20, "eval_duration": 0})
    model = OllamaChatModel(_client(_Transport(stream=_LinesStream([CHUNK_A, done]))))
    completion = asyncio.run(_collect(model))[-1]
    assert isinstance(completion, Completion)
    assert completion.eval_duration_ns == 0
    assert completion.tokens_per_second is None


def test_eval_duration_tipo_invalido_è_tipizzato() -> None:
    done = json.dumps(
        {"done": True, "done_reason": "stop", "eval_count": 20, "eval_duration": "lento"}
    )
    model = OllamaChatModel(_client(_Transport(stream=_LinesStream([CHUNK_A, done]))))
    with pytest.raises(InferenceFailed, match="metriche non valide"):
        asyncio.run(_collect(model))


def test_eval_duration_booleano_è_tipizzato() -> None:
    done = json.dumps(
        {"done": True, "done_reason": "stop", "eval_count": 20, "eval_duration": True}
    )
    model = OllamaChatModel(_client(_Transport(stream=_LinesStream([CHUNK_A, done]))))
    with pytest.raises(InferenceFailed, match="metriche non valide"):
        asyncio.run(_collect(model))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt_eval_count", "molti"),
        ("prompt_eval_count", -1),
        ("eval_count", 1.5),
        ("eval_count", False),
        ("eval_duration", -1),
    ],
)
def test_metrica_terminale_presente_ma_invalida_fallisce(field: str, value: object) -> None:
    payload: dict[str, object] = {
        "done": True,
        "done_reason": "stop",
        "eval_count": 20,
        "eval_duration": 2_000_000_000,
    }
    payload[field] = value
    model = OllamaChatModel(
        _client(_Transport(stream=_LinesStream([CHUNK_A, json.dumps(payload)])))
    )
    with pytest.raises(InferenceFailed, match="metriche non valide"):
        asyncio.run(_collect(model))


def test_disconnessione_a_meta_stream_è_tipizzata() -> None:
    stream = _LinesStream([CHUNK_A, CHUNK_B], fail_after=1, fail_with=httpx.ReadError)
    model = OllamaChatModel(_client(_Transport(stream=stream)))
    with pytest.raises(InferenceFailed, match="connessione"):
        asyncio.run(_drain(model))


async def _drain(model: OllamaChatModel) -> None:
    async for _ in model.stream(_request()):
        pass


async def _collect(model: OllamaChatModel) -> list[Any]:
    return [event async for event in model.stream(_request())]
