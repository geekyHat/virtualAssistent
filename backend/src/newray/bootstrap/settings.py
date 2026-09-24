"""Configurazione risolta e validata dall'ambiente (NewRay.md §21.2, §20.3).

Convenzione completa a partire da A-07: bind loopback di default, nessun
bind non-loopback su HTTP semplice senza cookie Secure (nessuna esposizione
remota senza TLS, §20.3), dati operativi in ``NEWRAY_DATA_DIR`` fuori dai
sorgenti. I DSN non compaiono mai nei log.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Address considerati loopback (bind di default, NewRay.md §20.3).
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


class Settings(BaseSettings):
    """Impostazioni applicative: ogni campo da variabile ``NEWRAY_*``."""

    model_config = SettingsConfigDict(env_prefix="NEWRAY_", extra="ignore")

    #: DSN del ruolo applicativo (``newray_app``), mai quello delle
    #: migrazioni: le credenziali sono distinte (NewRay.md §21.2).
    database_dsn: str = Field(description="DSN applicativo PostgreSQL (ruolo newray_app)")
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_pool_timeout: float = Field(default=3, gt=0, le=60)
    database_connect_timeout: int = Field(default=3, ge=1, le=60)
    database_statement_timeout_ms: int = Field(default=10_000, ge=1, le=300_000)
    database_lock_timeout_ms: int = Field(default=3_000, ge=1, le=300_000)
    database_idle_transaction_timeout_ms: int = Field(default=15_000, ge=1, le=300_000)
    #: Interfaccia di ascolto: loopback di default (§20.3).
    bind_address: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    #: Origine pubblica canonica della WebUI e dell'API same-origin.
    #: In locale coincide col bind loopback; una LAN richiede HTTPS esplicito.
    public_origin: str | None = None
    #: True quando l'origine è servita in HTTPS (ufficio/produzione).
    cookie_secure: bool = False
    #: Dati operativi fuori dai sorgenti e dagli asset statici (§21.2).
    data_dir: Path = Path.home() / ".local" / "share" / "newray"
    #: Base URL del runtime Ollama locale (B-03). Assente → nessun
    #: runtime collegato: catalogo vuoto e ``MODEL_UNAVAILABLE``
    #: recuperabile, mai un provider invisibile (ADR 0003).
    ollama_base_url: str | None = None
    #: Candidato pilot esplicito per il solo nuovo Assistente (ADR 0006).
    #: Non importa pesi e non sostituisce un binding storico. L'operatore
    #: può scegliere un valore diverso solo con configurazione esplicita.
    default_model_name: str | None = Field(
        default="newray-gemma4-31b-it:ud-q4-k-xl-vision-v1",
        max_length=200,
        pattern=r"^[^\s]+:[^\s:]+$",
    )
    #: Budget iniziale prudente; non modifica tag o Modelfile già installati.
    model_context_length: int = Field(default=8192, ge=1024, le=16384)
    model_max_context_length: int = Field(default=16384, ge=1024, le=32768)
    #: Limite di coda per scope (P-05, NewRay.md §8.4). Valore iniziale
    #: dichiarato, non misurato: da tarare in P-19/P-20.
    runs_max_queue_depth: int = Field(default=50, ge=1, le=10_000)
    #: Deadline complessiva di una generazione durevole (P-05). Valore
    #: iniziale dichiarato, non misurato.
    run_max_duration_seconds: int = Field(default=300, ge=1, le=3_600)

    @model_validator(mode="after")
    def _valida_budget_modello(self) -> Settings:
        if self.model_context_length > self.model_max_context_length:
            raise ValueError("NEWRAY_MODEL_CONTEXT_LENGTH supera il massimo consentito")
        return self

    @model_validator(mode="after")
    def _valida_esposizione(self) -> Settings:
        if self.database_lock_timeout_ms > self.database_statement_timeout_ms:
            raise ValueError(
                "database_lock_timeout_ms non può superare database_statement_timeout_ms"
            )
        # §20.3: nessun bind non-loopback senza TLS — in locale il bind resta
        # sull'interfaccia loopback e i cookie non viaggiano mai su HTTP
        # semplice verso la rete.
        if self.bind_address not in _LOOPBACK and not self.cookie_secure:
            raise ValueError(
                "NEWRAY_BIND_ADDRESS non-loopback richiede NEWRAY_COOKIE_SECURE=true"
                " (NewRay.md §20.3: nessuna esposizione remota senza TLS)"
            )
        default_host = f"[{self.bind_address}]" if ":" in self.bind_address else self.bind_address
        origin = self.public_origin or f"http://{default_host}:{self.port}"
        parsed_origin = urlparse(origin)
        try:
            origin_port = parsed_origin.port
        except ValueError as exc:
            raise ValueError("NEWRAY_PUBLIC_ORIGIN deve avere una porta valida") from exc
        if (
            parsed_origin.scheme not in {"http", "https"}
            or not parsed_origin.hostname
            or parsed_origin.username
            or parsed_origin.password
            or parsed_origin.path not in {"", "/"}
            or parsed_origin.params
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            raise ValueError("NEWRAY_PUBLIC_ORIGIN deve essere una sola origine http(s)")
        assert parsed_origin.hostname is not None
        origin_host = parsed_origin.hostname.lower()
        bracketed_host = f"[{origin_host}]" if ":" in origin_host else origin_host
        uses_default_port = (parsed_origin.scheme == "http" and origin_port == 80) or (
            parsed_origin.scheme == "https" and origin_port == 443
        )
        canonical_port = "" if origin_port is None or uses_default_port else f":{origin_port}"
        canonical_origin = f"{parsed_origin.scheme.lower()}://{bracketed_host}{canonical_port}"
        if self.bind_address not in _LOOPBACK and (
            not self.cookie_secure or parsed_origin.scheme != "https"
        ):
            raise ValueError(
                "bind non-loopback richiede NEWRAY_COOKIE_SECURE=true e NEWRAY_PUBLIC_ORIGIN HTTPS"
            )
        if self.bind_address in _LOOPBACK and origin_host not in _LOOPBACK:
            raise ValueError("un bind loopback richiede un'origine pubblica loopback")
        self.public_origin = canonical_origin
        # §21.2: i dati stanno fuori dai sorgenti; un percorso relativo
        # dipenderebbe dalla CWD e sarebbe un ambiguo.
        if not self.data_dir.is_absolute():
            raise ValueError("NEWRAY_DATA_DIR deve essere un percorso assoluto")
        # B-03: l'egress verso Ollama è un singolo base URL esplicito;
        # si attende un servizio locale su loopback (§20.3).
        if self.ollama_base_url is not None:
            parsed = urlparse(self.ollama_base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("NEWRAY_OLLAMA_BASE_URL deve essere un URL http(s) con host")
        return self
