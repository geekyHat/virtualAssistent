"""P-17: policy di egress — nessun URL esterno, nessun redirect, same-origin.

Test principalmente offline; sfruttano ``httpx.MockTransport`` per
osservare che il client non segua redirect e che i settings rifiutino
URL non locali per il runtime Ollama.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from newray.bootstrap.settings import Settings
from newray.infrastructure.network import HttpClient


def _local_settings(overrides: dict[str, object]) -> Settings:
    """Costruisce Settings iniettando esplicitamente le env; il validator
    accetta un bind loopback + origine coerente."""
    base: dict[str, object] = {
        "database_dsn": "postgresql+psycopg://a:b@127.0.0.1:5432/x",
        "bind_address": "127.0.0.1",
        "public_origin": "http://127.0.0.1:8000",
        "cookie_secure": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_httpclient_non_segue_redirect() -> None:
    """Il client HTTP costruito dall'app non segue redirect 302 verso host
    esterni: risponde con 302 e lascia al chiamante la responsabilità."""

    async def scenario() -> None:
        handler_calls: list[str] = []

        def transport_handler(request: httpx.Request) -> httpx.Response:
            handler_calls.append(str(request.url))
            if request.url.path == "/redirect":
                return httpx.Response(302, headers={"location": "http://malicious.example/steal"})
            # Se venisse seguito il redirect, questo handler riceverebbe
            # una request a malicious.example/steal.
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(transport_handler)
        async_client = httpx.AsyncClient(
            base_url="http://127.0.0.1:11434",
            transport=transport,
            timeout=httpx.Timeout(connect=1.0, read=1.0, write=1.0, pool=1.0),
        )
        client = HttpClient(
            base_url="http://127.0.0.1:11434",
            client=async_client,
        )
        try:
            response = await client.get_json("/redirect")
            # Il redirect NON è stato seguito.
            assert response.status_code == 302
            assert len(handler_calls) == 1
            assert "malicious.example" not in handler_calls[0]
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_httpclient_richiede_timeout_positivi() -> None:
    with pytest.raises(ValueError):
        HttpClient(base_url="http://127.0.0.1", connect_timeout=timedelta(0))


def test_settings_rifiuta_ollama_non_locale() -> None:
    """`NEWRAY_OLLAMA_BASE_URL` deve avere schema http/https e host valido;
    il validator non concede scheme senza host o URL vuoto."""
    # Loopback esplicito: passa.
    ok = _local_settings({"ollama_base_url": "http://127.0.0.1:11434"})
    assert ok.ollama_base_url == "http://127.0.0.1:11434"

    # Assente: consentito (nessun runtime collegato).
    ok = _local_settings({"ollama_base_url": None})
    assert ok.ollama_base_url is None

    # Schema non http(s): rifiutato.
    with pytest.raises((ValueError, ValidationError)):  # ValidationError o ValueError
        _local_settings({"ollama_base_url": "ftp://example/"})

    # Senza host: rifiutato.
    with pytest.raises((ValueError, ValidationError)):
        _local_settings({"ollama_base_url": "http:///"})


def test_settings_bind_non_loopback_richiede_tls() -> None:
    """Bind non-loopback in HTTP semplice → rifiutato all'avvio (§20.3)."""
    with pytest.raises((ValueError, ValidationError)):
        _local_settings(
            {
                "bind_address": "0.0.0.0",
                "public_origin": "http://0.0.0.0:8000",
                "cookie_secure": False,
            }
        )


def test_same_origin_middleware_rifiuta(hostile_client: TestClient) -> None:
    """POST con Origin estranea → rifiutato dal middleware same-origin."""
    r = hostile_client.post(
        "/api/v1/session",
        json={"display_name": "Ada", "credential": "test-passphrase-1234"},
        headers={"Origin": "http://malicious.example"},
    )
    # Il middleware risponde con 403 (o 400) per origine estranea; in
    # ogni caso non 201: la richiesta deve fallire prima di creare
    # l'owner.
    assert r.status_code != 201, "richiesta cross-origin non deve avere effetto"
    assert r.status_code in {400, 403}


def test_public_origin_richiede_percorso_pulito() -> None:
    """`public_origin` con query/path/frammento è respinta al bootstrap."""
    with pytest.raises((ValueError, ValidationError)):
        _local_settings({"public_origin": "http://127.0.0.1:8000/api"})
    with pytest.raises((ValueError, ValidationError)):
        _local_settings({"public_origin": "http://127.0.0.1:8000/?a=b"})
    with pytest.raises((ValueError, ValidationError)):
        _local_settings({"public_origin": "http://user:pw@127.0.0.1:8000"})
