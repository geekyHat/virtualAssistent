"""Client HTTP di egress controllato (NewRay.md §5; B-03).

Isola il vendor di rete (httpx): gli adapter (``modules/*/adapters``)
usano questo client senza conoscere il trasporto, e l'egress resta
limitato a un singolo base URL risolto all'avvio con timeout espliciti.

I guasti emergono come errori di trasporto (``NetworkError`` e sottotipi):
solo l'adapter proprietario li mappa sui codici stabili di dominio
(NewRay.md §6.1). Nei payload pubblici non compaiono mai dettagli vendor.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import httpx

__all__ = [
    "HttpClient",
    "NetworkError",
    "NetworkTimeout",
    "NetworkUnreachable",
    "Response",
    "StreamedResponse",
]


class NetworkError(Exception):
    """Guasto dell'egress controllato: nessun dettaglio vendor al dominio."""


class NetworkUnreachable(NetworkError):
    """Servizio di destinazione irraggiungibile (connessione rifiutata,
    DNS, protocollo non supportato)."""


class NetworkTimeout(NetworkError):
    """Connessione o lettura oltre i limiti di tempo espliciti."""


class Response:
    """Risposta HTTP con corpo JSON già analizzato (o ``None`` se non JSON)."""

    def __init__(self, status_code: int, data: object | None) -> None:
        self.status_code = status_code
        self.data = data


class StreamedResponse:
    """Risposta HTTP con corpo in streaming (righe, es. NDJSON).

    Chiudere il flusso (``aclose``) rilascia la connessione: per i servizi
    di inference la cancellazione della generazione passa da qui.
    """

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code

    async def text(self) -> str:
        """Corpo completo: solo per risposte di errore (piccole)."""
        body = await self._response.aread()
        return body.decode("utf-8", errors="replace")

    def lines(self) -> AsyncIterator[str]:
        async def _lines() -> AsyncIterator[str]:
            try:
                async for line in self._response.aiter_lines():
                    yield line
            except httpx.TimeoutException as exc:
                raise NetworkTimeout("lettura oltre il limite di tempo") from exc
            except httpx.HTTPError as exc:
                raise NetworkUnreachable("errore nella lettura dello streaming") from exc

        return _lines()

    async def aclose(self) -> None:
        await self._response.aclose()


class HttpClient:
    """Egress controllato: un solo base URL, timeout espliciti.

    ``client`` è iniettabile per i test di contratto (``httpx.MockTransport``
    non richiede rete: la suite ordinaria resta offline, NewRay.md §22.1).
    """

    def __init__(
        self,
        base_url: str,
        connect_timeout: timedelta = timedelta(seconds=5),
        read_timeout: timedelta = timedelta(seconds=120),
        write_timeout: timedelta = timedelta(seconds=20),
        pool_timeout: timedelta = timedelta(seconds=5),
        client: httpx.AsyncClient | None = None,
    ) -> None:
        for name, timeout in (
            ("connect_timeout", connect_timeout),
            ("read_timeout", read_timeout),
            ("write_timeout", write_timeout),
            ("pool_timeout", pool_timeout),
        ):
            if timeout <= timedelta(0):
                raise ValueError(f"HttpClient: {name} deve essere positivo")
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=connect_timeout.total_seconds(),
                read=read_timeout.total_seconds(),
                write=write_timeout.total_seconds(),
                pool=pool_timeout.total_seconds(),
            ),
        )
        self._validate_client_timeouts()

    async def get_json(self, path: str) -> Response:
        try:
            response = await self._client.get(path)
        except httpx.TimeoutException as exc:
            raise NetworkTimeout("timeout di connessione al servizio di destinazione") from exc
        except httpx.HTTPError as exc:
            raise NetworkUnreachable("servizio di destinazione irraggiungibile") from exc
        data: object | None
        try:
            data = response.json()
        except ValueError:
            data = None
        return Response(response.status_code, data)

    async def post_json(self, path: str, payload: dict[str, object]) -> Response:
        """POST JSON delimitato per diagnostica, senza stream né retry."""
        try:
            response = await self._client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise NetworkTimeout("timeout di connessione al servizio di destinazione") from exc
        except httpx.HTTPError as exc:
            raise NetworkUnreachable("servizio di destinazione irraggiungibile") from exc
        try:
            data = response.json()
        except ValueError:
            data = None
        return Response(response.status_code, data)

    async def post_stream(
        self,
        path: str,
        payload: dict[str, object],
        inactivity_timeout: timedelta | None = None,
    ) -> StreamedResponse:
        """POST con risposta in streaming e timeout fra chunk opzionale.

        Senza override non viene passato alcun ``timeout`` a HTTPX: la
        richiesta eredita tutti e quattro i limiti del client (connect, read,
        write e pool). L'override cambia soltanto ``read``, che in HTTPX è
        inattività di rete, non la durata massima della generazione.
        """
        if inactivity_timeout is not None and inactivity_timeout <= timedelta(0):
            raise ValueError("HttpClient: inactivity_timeout deve essere positivo")
        try:
            if inactivity_timeout is None:
                request = self._client.build_request("POST", path, json=payload)
            else:
                request = self._client.build_request(
                    "POST",
                    path,
                    json=payload,
                    timeout=httpx.Timeout(
                        connect=self._client.timeout.connect,
                        read=inactivity_timeout.total_seconds(),
                        write=self._client.timeout.write,
                        pool=self._client.timeout.pool,
                    ),
                )
            response = await self._client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise NetworkTimeout("timeout di connessione al servizio di destinazione") from exc
        except httpx.HTTPError as exc:
            raise NetworkUnreachable("servizio di destinazione irraggiungibile") from exc
        return StreamedResponse(response)

    def _validate_client_timeouts(self) -> None:
        """Rifiuta client iniettati che disabilitano un timeout HTTPX.

        ``None`` in HTTPX non significa «usa il default»: significa timeout
        disabilitato. Il prodotto non offre questa modalità all'adapter.
        """
        for name in ("connect", "read", "write", "pool"):
            value = getattr(self._client.timeout, name)
            if value is None or value <= 0:
                raise ValueError(f"HttpClient: timeout {name} deve essere positivo")

    async def aclose(self) -> None:
        await self._client.aclose()
