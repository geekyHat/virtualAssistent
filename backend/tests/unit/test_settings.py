"""Validazione dei settings (NewRay.md §21.2, §20.3 — A-07).

Gli invariati di esposizione: bind loopback di default, nessun bind
non-loopback su HTTP semplice senza cookie Secure, dati operativi in un
percorso assoluto fuori dai sorgenti.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from newray.bootstrap.settings import Settings


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    monkeypatch.setenv("NEWRAY_DATABASE_DSN", "postgresql+psycopg://newray_app@127.0.0.1/newray")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings()  # type: ignore[call-arg]


def test_default_sono_sicuri(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: loopback, porta 8000, dati assoluti in una home locale."""
    s = _settings(monkeypatch)
    assert s.bind_address == "127.0.0.1"
    assert s.port == 8000
    assert s.cookie_secure is False
    assert s.data_dir.is_absolute()


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "localhost"])
def test_bind_loopback_e_ammissibile(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    """Tutti gli indirizzi loopback sono ammessi anche su HTTP semplice."""
    s = _settings(monkeypatch, NEWRAY_BIND_ADDRESS=address)
    assert s.bind_address == address


def test_bind_non_loopback_richiede_cookie_secure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§20.3: nessun bind non-loopback senza TLS → rifiuto all'avvio."""
    with pytest.raises(ValidationError, match="NEWRAY_COOKIE_SECURE=true"):
        _settings(monkeypatch, NEWRAY_BIND_ADDRESS="0.0.0.0")


def test_bind_non_loopback_con_cookie_secure_e_ammissibile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un'esposizione non locale dichiara origine HTTPS e cookie Secure."""
    s = _settings(
        monkeypatch,
        NEWRAY_BIND_ADDRESS="0.0.0.0",
        NEWRAY_COOKIE_SECURE="true",
        NEWRAY_PUBLIC_ORIGIN="https://newray.example.test",
    )
    assert s.cookie_secure is True
    assert s.public_origin == "https://newray.example.test"


@pytest.mark.parametrize(
    "origin",
    ["http://newray.example.test", "https://newray.example.test/path", "not-an-origin"],
)
def test_bind_non_loopback_richiede_origine_https_canonica(
    monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    with pytest.raises(ValidationError, match="NEWRAY_PUBLIC_ORIGIN"):
        _settings(
            monkeypatch,
            NEWRAY_BIND_ADDRESS="0.0.0.0",
            NEWRAY_COOKIE_SECURE="true",
            NEWRAY_PUBLIC_ORIGIN=origin,
        )


def test_bind_loopback_rifiuta_origine_esterna(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="origine pubblica loopback"):
        _settings(monkeypatch, NEWRAY_PUBLIC_ORIGIN="https://newray.example.test")


def test_origine_canonica_normalizza_host_e_porta_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = _settings(monkeypatch, NEWRAY_PUBLIC_ORIGIN="HTTP://LOCALHOST:80/")
    assert s.public_origin == "http://localhost"


@pytest.mark.parametrize("port", ["0", "70000"])
def test_porta_fuori_range_e_rifiutata(monkeypatch: pytest.MonkeyPatch, port: str) -> None:
    with pytest.raises(ValidationError):
        _settings(monkeypatch, NEWRAY_PORT=port)


def test_data_dir_relativo_e_rifiutato(monkeypatch: pytest.MonkeyPatch) -> None:
    """§21.2: un path relativo dipenderebbe dalla CWD — ambiguo."""
    with pytest.raises(ValidationError, match="percorso assoluto"):
        _settings(monkeypatch, NEWRAY_DATA_DIR="dati")


def test_data_dir_assoluto_e_ammissibile(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch, NEWRAY_DATA_DIR="/srv/newray/dati")
    assert str(s.data_dir) == "/srv/newray/dati"


@pytest.mark.parametrize("model", ["", "llama3.1", "modello con spazi:q5", "modello:"])
def test_modello_iniziale_richiede_nome_completo(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    with pytest.raises(ValidationError):
        _settings(monkeypatch, NEWRAY_DEFAULT_MODEL_NAME=model)


def test_modello_iniziale_esplicito(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch, NEWRAY_DEFAULT_MODEL_NAME="newray-qwen3.8-27b:ud-q5-k-m")
    assert s.default_model_name == "newray-qwen3.8-27b:ud-q5-k-m"


def test_modello_pilot_di_default_non_sostituisce_config_esplicita(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _settings(monkeypatch).default_model_name == "newray-gemma4-31b-it:ud-q4-k-xl-vision-v1"


def test_budget_contesto_pilot_limitato(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    assert (settings.model_context_length, settings.model_max_context_length) == (8192, 16384)
    with pytest.raises(ValidationError):
        _settings(
            monkeypatch,
            NEWRAY_MODEL_CONTEXT_LENGTH="32768",
            NEWRAY_MODEL_MAX_CONTEXT_LENGTH="16384",
        )
