"""Sonde offline della revisione: osservazioni, non test di accettazione.

Dal root: backend/.venv/bin/python docs/evidence/code-review-2026-09-17-probes.py
Nessuna rete, GPU o connessione DB; il comando Alembic è intercettato.
"""

import asyncio
import importlib.util
import inspect
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = (
    Path(sys.argv[1]).resolve()
    if len(sys.argv) > 1
    else Path(__file__).resolve().parents[2]
)
sys.path[:0] = [
    str(ROOT / "backend/src"),
    str(ROOT / "backend/tests"),
    str(ROOT / "scripts"),
]

import httpx

import check_architecture as architecture
import qualify_local_models as qualification
from fakes import (
    FakeClock,
    InMemoryConversationRepository,
    InMemoryMessageStore,
    InMemoryModelBindingStore,
    InMemoryModelCatalog,
    InMemoryProfileRepository,
    InMemorySessionStore,
    InMemoryUserRepository,
)
from newray.bootstrap.api import create_app
from newray.bootstrap.settings import Settings
from newray.infrastructure.network import HttpClient
from newray.kernel.identity import Principal, Role, new_id
from newray.modules.conversations import ConversationService
from newray.modules.identity import IdentityService, Organization, User, Session
from newray.modules.models import (
    ChatMessage,
    ChatRequest,
    ChatRole,
    ModelInfo,
    ModelStatus,
)
from newray.modules.models.adapters.ollama import OllamaChatModel
from newray.modules.profiles import ProfileService


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Lines(httpx.AsyncByteStream):
    def __init__(self, chunks, fail=False):
        self.chunks, self.fail = chunks, fail

    async def __aiter__(self):
        for chunk in self.chunks:
            yield (json.dumps(chunk) + "\n").encode()
        if self.fail:
            raise httpx.ReadError("synthetic disconnect")


async def stream_probe(chunks, fail=False):
    observed = {}

    def respond(request):
        observed["timeouts"] = request.extensions["timeout"]
        return httpx.Response(200, stream=Lines(chunks, fail))

    transport = httpx.MockTransport(respond)
    raw = httpx.AsyncClient(
        base_url="http://synthetic", transport=transport, timeout=12
    )
    client = HttpClient("http://synthetic", client=raw)
    request = ChatRequest(
        model="synthetic:tag",
        runtime="ollama",
        messages=(ChatMessage(ChatRole.USER, "Ciao"),),
    )
    observed["events"] = []
    try:
        async for event in OllamaChatModel(client).stream(request):
            observed["events"].append(asdict(event))
    except Exception as exc:
        observed["error_type"] = type(exc).__name__
    finally:
        await client.aclose()
    return observed


async def main():
    result = {}
    fixture = load_module(
        "review_integration_fixtures", ROOT / "backend/tests/integration/conftest.py"
    )
    # URLs senza credenziali e nessuna chiamata reale a SQLAlchemy/Alembic.
    synthetic = "postgresql+psycopg://localhost/review_only"
    targets = []
    with (
        patch.object(fixture, "_require_env", return_value=(synthetic,) * 3),
        patch.object(
            fixture.command,
            "upgrade",
            side_effect=lambda cfg, revision: targets.append(
                fixture.migration_urls.database_name(
                    cfg.get_main_option("sqlalchemy.url")
                )
            ),
        ),
    ):
        generator = fixture.operational_database.__wrapped__()
        next(generator)
        generator.close()
    result["migration_fixture_upgrade_targets"] = targets

    clock = FakeClock(datetime(2026, 9, 17, tzinfo=UTC))
    users, sessions = InMemoryUserRepository(), InMemorySessionStore()
    # Identity è in modifica concorrente. Non qualificare qui il bootstrap:
    # inserire soltanto identità sintetiche nei fake per le sonde HTTP/profile.
    kwargs = (
        {"bootstrap": None}
        if "bootstrap" in inspect.signature(IdentityService).parameters
        else {}
    )
    identity = IdentityService(users, sessions, clock, **kwargs)
    org = Organization(new_id(), "Synthetic", clock.now())
    user = User(new_id(), org.id, "Synthetic", Role.OWNER, clock.now())
    session = Session(
        new_id(), user.id, org.id, clock.now(), clock.now() + timedelta(days=1)
    )
    users.add_organization(org)
    users.add_user(user)
    sessions.save(session)
    principal = Principal(user.id, org.id, session.id, Role.OWNER)
    repository, bindings = InMemoryProfileRepository(), InMemoryModelBindingStore()
    catalog = InMemoryModelCatalog(
        (
            ModelInfo(
                "synthetic:tag", "ollama", "synthetic-digest", ModelStatus.INSTALLED, ()
            ),
        )
    )
    profiles = ProfileService(
        repository, bindings, catalog, clock, default_model_name="synthetic:tag"
    )
    profiles.ensure_defaults(principal)
    profile = next(iter(repository._profiles.values()))
    snapshot = await profiles.resolve_binding(principal, profile.id)
    snapshot.parameters["temperature"] = 999
    result["snapshot_mutable"] = snapshot.parameters["temperature"] == 999
    result["snapshot_aliases_binding"] = (
        next(iter(bindings._bindings.values())).parameters == snapshot.parameters
    )

    partial, partial_bindings = InMemoryProfileRepository(), InMemoryModelBindingStore()
    partial_service = ProfileService(
        partial, partial_bindings, catalog, clock, default_model_name="synthetic:tag"
    )
    add_version, calls = partial.add_version, []

    def fail_on_second(version):
        calls.append(version)
        if len(calls) == 2:
            raise RuntimeError("synthetic failure")
        add_version(version)

    with patch.object(partial, "add_version", side_effect=fail_on_second):
        try:
            partial_service.ensure_defaults(principal)
        except RuntimeError:
            pass
    partial_service.ensure_defaults(principal)
    result["profiles_after_failure_and_retry"] = {
        "profiles": len(partial._profiles),
        "versions": len(partial._versions),
        "visible": len(partial.list_profiles(principal.scope)),
    }

    conversations = ConversationService(
        InMemoryConversationRepository(), InMemoryMessageStore(), clock
    )
    app = create_app(identity, conversations, profiles)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
    ) as client:
        client.cookies.set("newray_session", str(principal.session_id))
        invalid = await client.post("/api/v1/conversations", json={"title": ""})
        result["validation_error"] = {
            "status": invalid.status_code,
            "keys": list(invalid.json()),
        }
        revoked = await client.post(
            "/api/v1/session/revoke", headers={"Origin": "http://127.0.0.1:9999"}
        )
        me = await client.get("/api/v1/me")
        result["logout_then_me"] = [revoked.status_code, me.status_code]
        result["foreign_origin_revoke_status"] = revoked.status_code
    settings = Settings(
        database_dsn=synthetic, bind_address="0.0.0.0", cookie_secure=True
    )
    result["remote_bind_with_cookie_only"] = settings.bind_address
    rule = architecture.FileRule(
        ROOT / "backend/src/newray/modules/profiles/application.py",
        "module-core",
        "profiles",
    )
    result["forbidden_imports_not_detected"] = architecture.newray_violations(
        rule,
        ["newray.modules.profiles.adapters.postgres", "newray.infrastructure.network"],
    )
    request = ChatRequest(
        model="synthetic:tag",
        runtime="ollama",
        messages=(ChatMessage(ChatRole.USER, "Ciao"),),
        timeout=timedelta(seconds=-1),
    )
    result["negative_timeout_accepted"] = request.timeout.total_seconds()
    content = {"message": {"role": "assistant", "content": "Ciao"}, "done": False}
    done = {
        "message": {"role": "assistant", "content": ""},
        "done": True,
        "done_reason": "length",
    }
    result["ollama_length"] = await stream_probe([content, done])
    result["ollama_eof"] = await stream_probe([content])
    result["ollama_unknown_schema"] = await stream_probe([{"unexpected": True}])
    result["ollama_duplicate_completion"] = await stream_probe([done, done])
    result["ollama_disconnect"] = await stream_probe([content], fail=True)

    responses = {
        "/api/show": {"template": "synthetic"},
        "/api/tags": {"models": [{"name": "synthetic:tag", "size": 0}]},
        "/api/generate": {},
        "/api/ps": {"models": []},
    }
    closed = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def aclose(self):
            closed.append(True)

    def fake_api(host, path, *args, **kwargs):
        if path == "/api/chat":
            raise OSError("synthetic warmup failure")
        return responses[path]

    with (
        patch.object(qualification, "api", side_effect=fake_api),
        patch.object(qualification, "amd_vram_free", return_value=None),
        patch.object(qualification, "HttpClient", FakeClient),
    ):
        try:
            await qualification.qualify(
                {"id": "synthetic", "name": "synthetic:tag"},
                {"context_length": 8192},
                "http://synthetic",
                1,
            )
        except Exception as exc:
            result["qualification_warmup_failure"] = {
                "escaped": type(exc).__name__,
                "client_closed": bool(closed),
            }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
