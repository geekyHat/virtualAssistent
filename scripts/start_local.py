"""Avvio Linux: provisioning privato, readiness e supervisione di processi posseduti.

Eseguito da .start nell'ambiente locked. Il proprietario applicativo viene
ancora creato dalla WebUI; questo script prepara soltanto l'infrastruttura.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

import psycopg
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
APP_DSN = "NEWRAY_DATABASE_DSN"
MIGRATION_DSN = "NEWRAY_MIGRATION_DATABASE_URL"


class StartupError(Exception):
    """Messaggio operativo sicuro, senza credenziali."""


def load_env(path: Path, env: dict[str, str]) -> None:
    if not path.exists():
        return
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"(NEWRAY_[A-Z0-9_]+)=(.*)", line)
        if not match:
            raise StartupError(f".env, riga {number}: usare NEWRAY_CHIAVE=valore.")
        key, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env.setdefault(key, value)  # export esplicito prevale sul file; niente eval/source


def run(args: list[str], *, env: dict[str, str] | None = None, cwd: Path = ROOT) -> None:
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, timeout=180, check=False)
    if result.returncode:
        # Driver/installer possono stampare URL con credenziali.
        raise StartupError(
            f"{Path(args[0]).name}: operazione fallita (codice {result.returncode})."
        )


def port_free(number: int) -> bool:
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", number))
            return True
        except OSError:
            return False


def port(value: str) -> int:
    try:
        number = int(value)
        if 1024 <= number <= 65535:
            return number
    except ValueError:
        pass
    raise StartupError("Le porte locali devono essere numeri tra 1024 e 65535.")


def request(url: str, host: str | None = None) -> tuple[int, bytes]:
    headers = {"Host": host} if host else {}
    try:
        with build_opener(ProxyHandler({})).open(Request(url, headers=headers), timeout=2) as res:
            return res.status, res.read(2_000_000)
    except HTTPError as exc:
        with exc:
            return exc.code, exc.read(2_000_000)
    except (OSError, URLError):
        return 0, b""


def api_ready(url: str, host: str) -> bool:
    status, body = request(url + "/openapi.json", host)
    try:
        schema = json.loads(body)
        if status != 200 or schema["info"]["title"] != "NewRay":
            return False
        if "/api/v1/me" not in schema["paths"]:
            return False
        status, body = request(url + "/api/v1/me", host)
        payload = json.loads(body)
        return (status == 401 and payload.get("code") == "UNAUTHENTICATED") or (
            status == 200 and "user_id" in payload
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def web_ready(url: str, host: str, marker: str) -> bool:
    status, body = request(url + "/", host)
    return status == 200 and marker.encode() in body and api_ready(url, host)


def worker_ready(env: dict[str, str]) -> bool:
    """Readiness del worker dei run (P-05): nessuna porta HTTP propria,
    il segnale è la liveness registrata in ``run_workers`` (migrazione 0010).
    Una riga con heartbeat recente prova un processo vivo, non solo avviato.
    """
    try:
        engine = create_engine(env[APP_DSN], connect_args={"connect_timeout": 3})
    except Exception:
        return False
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT 1 FROM run_workers "
                    "WHERE last_heartbeat_at > now() - interval '10 seconds' LIMIT 1"
                )
            ).first()
            return row is not None
    except Exception:
        return False
    finally:
        engine.dispose()


def managed_config(state: Path) -> dict[str, str | int]:
    path = state / "database.json"
    if path.exists():
        if path.stat().st_mode & 0o077:
            raise StartupError(f"Proteggi {path} con chmod 600 prima di continuare.")
        return json.loads(path.read_text())
    if (state / "postgres").exists():
        raise StartupError(
            "Cluster presente senza credenziali: ripristinare database.json dal backup."
        )
    chosen = next((candidate for candidate in range(55432, 55532) if port_free(candidate)), None)
    if chosen is None:
        raise StartupError("Nessuna porta disponibile per il database locale (55432–55531).")
    config = {
        "port": chosen,
        "admin": secrets.token_hex(32),
        "migrate": secrets.token_hex(32),
        "app": secrets.token_hex(32),
    }
    fd, temporary = tempfile.mkstemp(dir=state)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(config, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return config


def ensure_database(state: Path, env: dict[str, str]) -> None:
    if env.get(APP_DSN) or env.get(MIGRATION_DSN):
        if not env.get(APP_DSN) or not env.get(MIGRATION_DSN):
            raise StartupError("Configurazione parziale: servono entrambi i DSN, oppure nessuno.")
        return  # Mai sostituire un database esplicitamente configurato.
    for tool in ("pg_config", "initdb", "pg_ctl"):
        if not shutil.which(tool):
            raise StartupError("Installa PostgreSQL e pgvector, poi riesegui ./.start --web.")
    sharedir = subprocess.check_output(["pg_config", "--sharedir"], text=True).strip()
    if not (Path(sharedir) / "extension/vector.control").is_file():
        raise StartupError("Installa pgvector per il PostgreSQL attivo, poi riprova.")
    config = managed_config(state)
    pgdata = state / "postgres"
    db_port = int(config["port"])
    if not (pgdata / "PG_VERSION").exists():
        print("Preparo il database locale…", flush=True)
        fd, pwfile = tempfile.mkstemp(dir=state)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(str(config["admin"]))
            run(
                [
                    "initdb",
                    "-D",
                    str(pgdata),
                    "-U",
                    "newray_admin",
                    "--encoding=UTF8",
                    "--auth-local=scram-sha-256",
                    "--auth-host=scram-sha-256",
                    "--pwfile",
                    pwfile,
                ]
            )
        finally:
            Path(pwfile).unlink(missing_ok=True)
    status = subprocess.run(
        ["pg_ctl", "-D", str(pgdata), "status"], capture_output=True, check=False
    )
    if status.returncode:
        if not port_free(db_port):
            raise StartupError(
                f"Porta database {db_port} occupata: nessun processo è stato fermato."
            )
        print("Avvio PostgreSQL…", flush=True)
        run(
            [
                "pg_ctl",
                "-D",
                str(pgdata),
                "-l",
                str(state / "postgres.log"),
                "-o",
                f"-h 127.0.0.1 -p {db_port} -k ''",
                "-w",
                "-t",
                "30",
                "start",
            ]
        )
    with psycopg.connect(
        host="127.0.0.1",
        port=db_port,
        user="newray_admin",
        password=str(config["admin"]),
        dbname="postgres",
        connect_timeout=3,
        autocommit=True,
    ) as conn:
        for role, password in (
            ("newray_migrate", config["migrate"]),
            ("newray_app", config["app"]),
        ):
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                conn.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER "
                        "NOCREATEDB NOCREATEROLE NOBYPASSRLS"
                    ).format(sql.Identifier(role), sql.Literal(password))
                )
        # Ruolo interno NOLOGIN: possiede solo le funzioni SECURITY DEFINER
        # del worker durevole (P-05, ADR 0007). newray_migrate ne diventa
        # membro solo per poter riassegnare la proprietà di quelle funzioni
        # dalle migrazioni; newray_app resta senza BYPASSRLS.
        if not conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = 'newray_scheduler'"
        ).fetchone():
            conn.execute(
                "CREATE ROLE newray_scheduler NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOLOGIN BYPASSRLS"
            )
        conn.execute("GRANT newray_scheduler TO newray_migrate")
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = 'newray'").fetchone():
            conn.execute("CREATE DATABASE newray OWNER newray_migrate")
    with psycopg.connect(
        host="127.0.0.1",
        port=db_port,
        user="newray_admin",
        password=str(config["admin"]),
        dbname="newray",
        connect_timeout=3,
        autocommit=True,
    ) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for key, role, password in (
        (APP_DSN, "newray_app", config["app"]),
        (MIGRATION_DSN, "newray_migrate", config["migrate"]),
    ):
        env[key] = f"postgresql+psycopg://{role}:{password}@127.0.0.1:{db_port}/newray"


def migrate(env: dict[str, str], *, api_running: bool) -> None:
    app_url, migration_url = make_url(env[APP_DSN]), make_url(env[MIGRATION_DSN])
    if (app_url.host, app_url.port, app_url.database) != (
        migration_url.host,
        migration_url.port,
        migration_url.database,
    ) or app_url.username == migration_url.username:
        raise StartupError("I DSN devono usare ruoli distinti sullo stesso database.")
    engine = create_engine(app_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            role = conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).one()
            owns_db = conn.execute(
                text(
                    "SELECT pg_get_userbyid(datdba) = current_user "
                    "FROM pg_database WHERE datname = current_database()"
                )
            ).scalar()
            if any(role) or owns_db:
                raise StartupError(
                    "Il ruolo applicativo non può essere owner, superuser o BYPASSRLS."
                )
    finally:
        engine.dispose()
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/migrations"))
    config.set_main_option("sqlalchemy.url", env[MIGRATION_DSN].replace("%", "%%"))
    if api_running:
        engine = create_engine(migration_url, connect_args={"connect_timeout": 3})
        try:
            with engine.connect() as conn:
                revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            if revision != ScriptDirectory.from_config(config).get_current_head():
                raise StartupError("Ferma l'API esistente e riprova per aggiornare il database.")
        finally:
            engine.dispose()
    else:
        print("Verifico le migrazioni…", flush=True)
        command.upgrade(config, "head")


class Supervisor:
    def __init__(self) -> None:
        self.children: list[subprocess.Popen] = []

    def start(self, args: list[str], env: dict[str, str], cwd: Path) -> subprocess.Popen:
        child = subprocess.Popen(args, env=env, cwd=cwd, start_new_session=True)
        self.children.append(child)
        return child

    def wait_ready(self, check, child: subprocess.Popen, name: str) -> None:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise StartupError(f"{name} si è arrestato durante l'avvio.")
            if check():
                return
            time.sleep(0.25)
        raise StartupError(f"{name} non pronto entro 40 secondi.")

    def close(self) -> None:
        for child in reversed(self.children):
            if child.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        os.killpg(child.pid, signal.SIGKILL)
                    child.wait()


def launch(supervisor: Supervisor) -> None:
    env = dict(os.environ)
    load_env(Path(env.get("NEWRAY_ENV_FILE", str(ROOT / ".env"))), env)
    api_port = port(env.get("NEWRAY_PORT", "8000"))
    web_port = port(env.get("NEWRAY_WEB_PORT", "5173"))
    if api_port == web_port:
        raise StartupError("API e WebUI devono usare porte diverse.")
    origin = f"http://127.0.0.1:{web_port}"
    if env.get("NEWRAY_PUBLIC_ORIGIN", origin) != origin:
        raise StartupError(f"Per l'avvio locale NEWRAY_PUBLIC_ORIGIN deve essere {origin}.")
    if env.get("NEWRAY_COOKIE_SECURE", "false").lower() not in ("false", "0"):
        raise StartupError("L'avvio locale HTTP richiede NEWRAY_COOKIE_SECURE=false.")
    env.update(
        NEWRAY_PUBLIC_ORIGIN=origin,
        NEWRAY_BIND_ADDRESS="127.0.0.1",
        NEWRAY_PORT=str(api_port),
    )
    data = Path(env.get("NEWRAY_DATA_DIR", str(Path.home() / ".local/share/newray")))
    if not data.is_absolute():
        raise StartupError("NEWRAY_DATA_DIR deve essere assoluto.")
    if data.resolve().is_relative_to(ROOT):
        raise StartupError("NEWRAY_DATA_DIR deve restare fuori dai sorgenti del progetto.")
    state = data / "launcher"
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    with (state / "startup.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StartupError(
                "Un altro avvio è in corso; attendi qualche secondo e riprova."
            ) from None
        host, api = f"127.0.0.1:{web_port}", f"http://127.0.0.1:{api_port}"
        marker = hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
        api_running = api_ready(api, host)
        if not api_running and not port_free(api_port):
            raise StartupError(f"Porta API {api_port} occupata da un servizio incompatibile.")
        web_running = web_ready(origin, host, marker)
        if not web_running and not port_free(web_port):
            code, body = request(origin)
            web_running = code == 200 and marker.encode() in body
            if not web_running:
                raise StartupError(f"Porta WebUI {web_port} occupata da un servizio incompatibile.")
        ensure_database(state, env)
        migrate(env, api_running=api_running)
        public_env = {k: v for k, v in env.items() if not k.startswith("NEWRAY_")}
        ollama = env.get("NEWRAY_OLLAMA_BASE_URL")
        if ollama and request(ollama.rstrip("/") + "/api/version")[0] != 200:
            parsed = urlparse(ollama)
            if (
                parsed.scheme != "http"
                or parsed.hostname != "127.0.0.1"
                or parsed.path not in ("", "/")
            ):
                raise StartupError(
                    "Runtime Ollama configurato non raggiungibile; verificare il servizio remoto."
                )
            if not shutil.which("ollama"):
                raise StartupError("Installa Ollama per il runtime configurato e riprova.")
            ollama_env = dict(public_env)
            ollama_env.update({k: v for k, v in env.items() if k.startswith("NEWRAY_OLLAMA_")})
            ollama_env["NEWRAY_OLLAMA_PORT"] = str(parsed.port or 80)
            print("Avvio Ollama…", flush=True)
            child = supervisor.start(
                [str(ROOT / "scripts/serve_ollama_local.sh")], ollama_env, ROOT
            )
            supervisor.wait_ready(
                lambda: request(ollama.rstrip("/") + "/api/version")[0] == 200,
                child,
                "Ollama",
            )
        if not api_running:
            api_env = dict(env)
            api_env.pop(MIGRATION_DSN, None)
            api_env.pop("NEWRAY_DB_MIGRATE_PASSWORD", None)
            print("Avvio API…", flush=True)
            child = supervisor.start(
                [sys.executable, "-m", "newray.bootstrap.main"],
                api_env,
                ROOT / "backend",
            )
            supervisor.wait_ready(lambda: api_ready(api, host), child, "API")
        else:
            print("API già attiva: riutilizzo il servizio.", flush=True)
        if not worker_ready(env):
            worker_env = dict(env)
            worker_env.pop(MIGRATION_DSN, None)
            worker_env.pop("NEWRAY_DB_MIGRATE_PASSWORD", None)
            print("Avvio worker run…", flush=True)
            child = supervisor.start(
                [sys.executable, "-m", "newray.bootstrap.worker"],
                worker_env,
                ROOT / "backend",
            )
            supervisor.wait_ready(lambda: worker_ready(env), child, "Worker run")
        else:
            print("Worker run già attivo: riutilizzo il servizio.", flush=True)
        if not web_running:
            lock_hash = hashlib.sha256((ROOT / "web/package-lock.json").read_bytes()).hexdigest()
            stamp = ROOT / "web/node_modules/.newray-lock"
            if (
                not stamp.exists()
                or stamp.read_text() != lock_hash
                or not (ROOT / "web/node_modules/vite/bin/vite.js").exists()
            ):
                print("Preparo le dipendenze WebUI dal lockfile…", flush=True)
                run(["npm", "ci", "--ignore-scripts"], env=public_env, cwd=ROOT / "web")
                stamp.write_text(lock_hash)
            public_env.update(NEWRAY_PORT=str(api_port), NEWRAY_WEB_MARKER=marker)
            print("Avvio WebUI…", flush=True)
            child = supervisor.start(
                [
                    "node",
                    "node_modules/vite/bin/vite.js",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(web_port),
                    "--strictPort",
                ],
                public_env,
                ROOT / "web",
            )
            supervisor.wait_ready(lambda: web_ready(origin, host, marker), child, "WebUI")
        elif not web_ready(origin, host, marker):
            raise StartupError("WebUI attiva ma proxy API incompatibile: riavviare quella WebUI.")
        print(f"NewRay pronto: {origin}/", flush=True)
    if not supervisor.children:
        print("Tutti i servizi sono già attivi.", flush=True)
        return
    print(
        "Ctrl-C ferma i processi avviati qui. Il database e i dati restano disponibili.",
        flush=True,
    )
    while all(child.poll() is None for child in supervisor.children):
        time.sleep(0.5)
    raise StartupError("Un servizio si è arrestato. Riesegui ./.start --web per riavviarlo.")


def interrupted(*_) -> None:
    raise KeyboardInterrupt


def main() -> int:
    supervisor = Supervisor()
    signal.signal(signal.SIGTERM, interrupted)
    try:
        launch(supervisor)
        return 0
    except KeyboardInterrupt:
        print("\nArresto dei servizi avviati…", flush=True)
        return 0
    except StartupError as exc:
        print(f"Avvio interrotto: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- confine CLI: non stampare DSN dai driver
        print(
            f"Avvio interrotto ({type(exc).__name__}). Verifica configurazione, "
            "credenziali e disponibilità del database; nessun dato è stato cancellato.",
            file=sys.stderr,
        )
        return 1
    finally:
        supervisor.close()


if __name__ == "__main__":
    sys.exit(main())
