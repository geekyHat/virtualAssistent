"""Regressioni del launcher; smoke reale opt-in, dati esclusivamente sintetici."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import start_local as start  # noqa: E402


class LauncherTests(unittest.TestCase):
    def test_config_never_executes_shell_and_export_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('NEWRAY_PORT=9999\nNEWRAY_VALUE="$(touch stolen)"\n')
            env = {"NEWRAY_PORT": "8888"}
            start.load_env(path, env)
            self.assertEqual(env["NEWRAY_PORT"], "8888")
            self.assertEqual(env["NEWRAY_VALUE"], "$(touch stolen)")
            self.assertFalse((Path(directory) / "stolen").exists())

    def test_partial_explicit_database_never_creates_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with self.assertRaises(start.StartupError):
                start.ensure_database(state, {start.APP_DSN: "configured"})
            self.assertEqual(list(state.iterdir()), [])

    def test_credentials_persist_private_and_orphan_cluster_is_preserved(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(start, "port_free", return_value=True),
        ):
            state = Path(directory)
            first = start.managed_config(state)
            self.assertEqual(start.managed_config(state), first)
            self.assertNotEqual(first["migrate"], first["app"])
            self.assertEqual((state / "database.json").stat().st_mode & 0o777, 0o600)
            (state / "database.json").unlink()
            (state / "postgres").mkdir()
            with self.assertRaises(start.StartupError):
                start.managed_config(state)
            self.assertTrue((state / "postgres").exists())

    def test_unrelated_http_401_is_not_a_ready_api(self):
        with patch.object(start, "request", return_value=(401, b'{"code":"UNAUTHENTICATED"}')):
            self.assertFalse(start.api_ready("http://unused", "unused"))

    def test_api_requires_schema_and_session_contract(self):
        schema = json.dumps({"info": {"title": "NewRay"}, "paths": {"/api/v1/me": {}}}).encode()
        with patch.object(
            start,
            "request",
            side_effect=[(200, schema), (401, b'{"code":"UNAUTHENTICATED"}')],
        ):
            self.assertTrue(start.api_ready("http://unused", "unused"))

    def test_startup_failure_cleans_only_owned_children(self):
        supervisor = start.Supervisor()
        child = supervisor.start(
            [sys.executable, "-c", "import time; time.sleep(30)"], dict(os.environ), start.ROOT
        )
        try:
            failed = supervisor.start(
                [sys.executable, "-c", "raise SystemExit(1)"], dict(os.environ), start.ROOT
            )
            failed.wait(timeout=5)
            with self.assertRaises(start.StartupError):
                supervisor.wait_ready(lambda: False, failed, "synthetic")
        finally:
            supervisor.close()
        self.assertIsNotNone(child.poll())

    def test_online_migration_preserves_url_encoded_password(self):
        # Ferma prima della connessione: osserva il DSN risolto dall'env Alembic.
        from alembic import command
        from alembic.config import Config
        from sqlalchemy.engine import make_url

        config = Config(str(start.ROOT / "backend/alembic.ini"))
        config.set_main_option("script_location", str(start.ROOT / "backend/migrations"))
        dsn = "postgresql+psycopg://migrate:synthetic%40%25%27@127.0.0.1/example"
        config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))

        def intercept(section, **_):
            self.assertEqual(make_url(section["sqlalchemy.url"]).password, "synthetic@%'")
            raise RuntimeError("stop-before-connect")

        with patch("sqlalchemy.engine_from_config", side_effect=intercept):
            with self.assertRaisesRegex(RuntimeError, "stop-before-connect"):
                command.upgrade(config, "head")


@unittest.skipUnless(
    os.environ.get("NEWRAY_LIVE_START_TEST") == "1", "opt-in PostgreSQL/processi reali"
)
class LiveLauncherTests(unittest.TestCase):
    def test_first_start_reuse_restart_and_data_survive(self):
        with tempfile.TemporaryDirectory(prefix="newray-launcher-test-") as directory:
            data = Path(directory)
            sockets = [socket.socket(), socket.socket()]
            try:
                for sock in sockets:
                    sock.bind(("127.0.0.1", 0))
                api_port, web_port = [sock.getsockname()[1] for sock in sockets]
            finally:
                for sock in sockets:
                    sock.close()
            env = {key: value for key, value in os.environ.items() if not key.startswith("NEWRAY_")}
            env.update(
                NEWRAY_DATA_DIR=str(data),
                NEWRAY_ENV_FILE=str(data / "empty.env"),
                NEWRAY_PORT=str(api_port),
                NEWRAY_WEB_PORT=str(web_port),
            )
            origin = f"http://127.0.0.1:{web_port}"
            processes = []

            def _worker_env() -> dict[str, str] | None:
                # Il launcher scrive le credenziali risolte in
                # database.json (P-05): il worker non espone porte HTTP,
                # la sua readiness si legge dalla stessa liveness DB che
                # usa ``start.worker_ready`` nel processo del launcher.
                path = data / "launcher/database.json"
                if not path.exists():
                    return None
                config = json.loads(path.read_text())
                dsn = (
                    f"postgresql+psycopg://newray_app:{config['app']}"
                    f"@127.0.0.1:{config['port']}/newray"
                )
                return {start.APP_DSN: dsn}

            def launch():
                log = (data / f"launcher-{len(processes)}.log").open("w")
                process = subprocess.Popen(
                    [str(start.ROOT / ".start"), "--web"],
                    cwd="/tmp",
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                log.close()
                processes.append(process)
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    self.assertIsNone(process.poll(), "launcher exited before readiness")
                    code, body = start.request(origin)
                    if (
                        code == 200
                        and b"newray-workspace" in body
                        and start.api_ready(origin, origin[7:])
                    ):
                        worker_env = _worker_env()
                        if worker_env is not None and start.worker_ready(worker_env):
                            return process
                    time.sleep(0.25)
                self.fail("launcher timeout (API/WebUI pronti, worker run mai registrato)")

            try:
                first = launch()
                req = Request(
                    origin + "/api/v1/session",
                    data=b'{"display_name":"Synthetic owner","credential":"test-passphrase-1234"}',
                    headers={"Content-Type": "application/json", "Origin": origin},
                )
                with urlopen(req, timeout=5) as response:
                    self.assertEqual(response.status, 201)
                    cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                    identity = json.load(response)
                again = subprocess.run(
                    [str(start.ROOT / ".start"), "--web"],
                    cwd="/tmp",
                    env=env,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(again.returncode, 0, again.stdout.decode())
                self.assertIn(b"Tutti i servizi sono", again.stdout)
                self.assertIsNone(first.poll())
                first.terminate()
                self.assertEqual(first.wait(timeout=20), 0)
                self.assertTrue(start.port_free(api_port))
                self.assertTrue(start.port_free(web_port))
                pgdata = data / "launcher/postgres"
                start.run(["pg_ctl", "-D", str(pgdata), "-m", "fast", "-w", "stop"])
                launch()
                with urlopen(
                    Request(origin + "/api/v1/me", headers={"Cookie": cookie}),
                    timeout=5,
                ) as response:
                    self.assertEqual(json.load(response)["user_id"], identity["user_id"])
                self.assertEqual((data / "launcher/database.json").stat().st_mode & 0o777, 0o600)
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=20)
                pgdata = data / "launcher/postgres"
                if (pgdata / "PG_VERSION").exists():
                    subprocess.run(
                        ["pg_ctl", "-D", str(pgdata), "-m", "fast", "-w", "stop"],
                        capture_output=True,
                        check=False,
                    )


if __name__ == "__main__":
    unittest.main()
