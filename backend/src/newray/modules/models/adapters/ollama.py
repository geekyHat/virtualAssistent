"""Adapter Ollama: catalogo locale e chat in streaming (B-03).

Implementa le porte ``ModelCatalog`` e ``ChatModel`` sull'API HTTP di
Ollama (``GET /api/tags``, ``POST /api/chat`` con risposta NDJSON in
streaming). I guasti vendor sono mappati sui codici stabili di dominio
(NewRay.md §6.1): nei payload pubblici non compaiono dettagli del vendor.

Servizio locale: nessuna dipendenza da egress esterno quando i modelli
sono installati (NewRay.md §22.2). Cancellazione: la chiusura dello
stream rilascia la connessione e Ollama interrompe la generazione.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from newray.infrastructure.network import (
    HttpClient,
    NetworkTimeout,
    NetworkUnreachable,
)
from newray.kernel.errors import InferenceFailed, InferenceTimeout, ModelUnavailable

from ..domain import (
    RUNTIME_OLLAMA,
    ChatRequest,
    Completion,
    ContentDelta,
    ModelInfo,
    ModelReadiness,
    ModelStatus,
    ReadinessState,
    StreamEvent,
)


class OllamaModelCatalog:
    """Catalogo dallo stato di Ollama: modelli installati con digest (§9.1).

    Runtime irraggiungibile → catalogo vuoto (stato onesto della
    macchina: nessun modello installato); timeout → ``InferenceTimeout``
    (guasto transitorio da esporre, non da nascondere).
    """

    def __init__(self, client: HttpClient) -> None:
        self._client = client

    async def readiness(self, model_name: str | None) -> ModelReadiness:
        """Diagnostica read-only; dichiarazione vendor != prova live.

        Non carica pesi. Una risposta tags/show malformata non diventa
        automaticamente "catalogo vuoto" o "modello incompatibile".
        """
        if not model_name:
            return ModelReadiness(ReadinessState.NOT_CONFIGURED, None)
        try:
            tags = await self._client.get_json("/api/tags")
        except (NetworkTimeout, NetworkUnreachable):
            return ModelReadiness(ReadinessState.UNREACHABLE, model_name)
        if tags.status_code != 200 or not isinstance(tags.data, dict):
            return ModelReadiness(ReadinessState.RUNTIME_ERROR, model_name)
        models = tags.data.get("models")
        if not isinstance(models, list):
            return ModelReadiness(ReadinessState.RUNTIME_ERROR, model_name)
        if not models:
            return ModelReadiness(ReadinessState.CATALOG_EMPTY, model_name)
        installed = next(
            (item for item in models if isinstance(item, dict) and item.get("name") == model_name),
            None,
        )
        if installed is None:
            return ModelReadiness(ReadinessState.MODEL_MISSING, model_name)
        digest = installed.get("digest")
        digest = digest if isinstance(digest, str) and digest else None
        try:
            shown = await self._client.post_json("/api/show", {"model": model_name})
        except (NetworkTimeout, NetworkUnreachable):
            return ModelReadiness(ReadinessState.UNREACHABLE, model_name, digest)
        if shown.status_code == 404:
            return ModelReadiness(ReadinessState.MODEL_MISSING, model_name)
        if shown.status_code != 200 or not isinstance(shown.data, dict):
            return ModelReadiness(ReadinessState.RUNTIME_ERROR, model_name, digest)
        raw_capabilities = shown.data.get("capabilities")
        if not isinstance(raw_capabilities, list) or not all(
            isinstance(item, str) for item in raw_capabilities
        ):
            return ModelReadiness(ReadinessState.RUNTIME_ERROR, model_name, digest)
        capabilities = tuple(raw_capabilities)
        state = (
            ReadinessState.ARTIFACT_INCOMPATIBLE
            if capabilities and "completion" not in capabilities
            else ReadinessState.INSTALLED_UNVERIFIED
        )
        return ModelReadiness(state, model_name, digest, capabilities)

    async def list_models(self) -> list[ModelInfo]:
        try:
            response = await self._client.get_json("/api/tags")
        except NetworkTimeout as exc:
            raise InferenceTimeout("timeout sul runtime ollama") from exc
        except NetworkUnreachable:
            return []
        if response.status_code != 200:
            raise InferenceFailed(f"errore del runtime ollama (HTTP {response.status_code})")
        models = response.data.get("models") if isinstance(response.data, dict) else None
        if not isinstance(models, list):
            raise InferenceFailed("risposta non valida dal runtime ollama")
        result: list[ModelInfo] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            digest = item.get("digest")
            # Stato onesto: Ollama dice che il modello è installato;
            # compatible/qualified richiedono prove registrate (§9.1).
            result.append(
                ModelInfo(
                    name=name,
                    runtime=RUNTIME_OLLAMA,
                    digest=digest if isinstance(digest, str) else None,
                    status=ModelStatus.INSTALLED,
                    capabilities=(),
                )
            )
        return result


class OllamaChatModel:
    """Chat via ``POST /api/chat``: NDJSON in streaming.

    Ogni riga è un chunk: ``message.content`` → ``ContentDelta``, la riga
    finale con ``done`` → ``Completion`` con la contabilizzazione token.
    I limiti della richiesta sono applicati al runtime (``options``).
    """

    def __init__(
        self,
        client: HttpClient,
        *,
        context_length: int = 8192,
        max_context_length: int = 16384,
    ) -> None:
        if context_length < 1024 or max_context_length < context_length:
            raise ValueError("budget di contesto Ollama non valido")
        self._client = client
        self._context_length = context_length
        self._max_context_length = max_context_length

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        payload: dict[str, object] = {
            "model": request.model,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
            "stream": True,
            # Contratto B-03.2-18: la prima UI non espone ragionamenti
            # privati come eventi separati. Disabilitiamo quindi thinking
            # esplicitamente, invece di consumare token/latency e scartare
            # ``message.thinking`` senza informare l'utente.
            "think": False,
        }
        options: dict[str, object] = dict(request.parameters)
        requested_context = options.get("num_ctx", self._context_length)
        if (
            isinstance(requested_context, bool)
            or not isinstance(requested_context, int)
            or requested_context < 1024
            or requested_context > self._max_context_length
        ):
            raise InferenceFailed("contesto richiesto fuori dal budget configurato")
        options["num_ctx"] = requested_context
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        if options:
            payload["options"] = options

        try:
            response = await self._client.post_stream(
                "/api/chat", payload, inactivity_timeout=request.inactivity_timeout
            )
        except NetworkTimeout as exc:
            raise InferenceTimeout("timeout sul runtime ollama") from exc
        except NetworkUnreachable as exc:
            raise ModelUnavailable("runtime ollama irraggiungibile") from exc

        try:
            if response.status_code == 404:
                raise ModelUnavailable(
                    f"modello {request.model!r} non disponibile nel runtime ollama"
                )
            if response.status_code != 200:
                raise InferenceFailed(f"errore del runtime ollama (HTTP {response.status_code})")
            completed = False
            try:
                async for line in response.lines():
                    if completed:
                        raise InferenceFailed(
                            "dati ricevuti dopo il completamento del runtime ollama"
                        )
                    event = _parse_chunk(line)
                    if event is None:
                        continue
                    yield event
                    completed = isinstance(event, Completion)
            except NetworkTimeout as exc:
                raise InferenceTimeout("timeout durante la generazione") from exc
            except NetworkUnreachable as exc:
                # Il runtime era raggiungibile all'avvio della richiesta ma è
                # caduto durante il body: non è un modello assente e non può
                # attraversare la porta come errore infrastrutturale grezzo.
                raise InferenceFailed("connessione al runtime ollama interrotta") from exc
            if not completed:
                raise InferenceFailed("stream terminato senza completamento dal runtime ollama")
        finally:
            await response.aclose()


def _parse_chunk(line: str) -> StreamEvent | None:
    """Analizza una riga NDJSON di Ollama in eventi di stream.

    Contratto vendor (API /api/chat): chunk con ``message.content``
    progressivo e riga finale con ``done`` e conteggi token. Una riga
    non valida o un ``error`` nel flusso sono guasti tipizzati, non
    ignorati.
    """
    text = line.strip()
    if not text:
        return None
    try:
        chunk: Any = json.loads(text)
    except ValueError as exc:
        raise InferenceFailed("risposta non valida dal runtime ollama") from exc
    if not isinstance(chunk, dict):
        raise InferenceFailed("risposta non valida dal runtime ollama")
    if "error" in chunk:
        raise InferenceFailed("errore durante la generazione dal runtime ollama")
    done = chunk.get("done")
    if not isinstance(done, bool):
        raise InferenceFailed("risposta non valida dal runtime ollama")
    if done:
        reason = chunk.get("done_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise InferenceFailed("completamento senza done_reason valido dal runtime ollama")
        return Completion(
            finish_reason=reason,
            prompt_tokens=_optional_nonnegative_int(chunk, "prompt_eval_count"),
            completion_tokens=_optional_nonnegative_int(chunk, "eval_count"),
            eval_duration_ns=_optional_nonnegative_int(chunk, "eval_duration"),
        )
    message = chunk.get("message")
    if not isinstance(message, dict):
        raise InferenceFailed("risposta non valida dal runtime ollama")
    content = message.get("content")
    if not isinstance(content, str):
        raise InferenceFailed("risposta non valida dal runtime ollama")
    return ContentDelta(text=content) if content else None


def _optional_nonnegative_int(chunk: dict[str, Any], field: str) -> int | None:
    """Legge una misura terminale opzionale senza mascherare schema errato.

    L'assenza è supportata dal contratto Ollama e diventa ``None``. Se il
    runtime invia il campo, invece, solo un intero non negativo è valido:
    booleani, stringhe e valori negativi sono un terminale malformato, non
    una misura semplicemente indisponibile.
    """
    if field not in chunk:
        return None
    value = chunk[field]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InferenceFailed("completamento con metriche non valide dal runtime ollama")
    return value
