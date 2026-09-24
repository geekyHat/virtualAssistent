"""Il contratto OpenAPI versionato deve restare coerente con i DTO (NewRay.md §19.1).

Il test è offline e non richiede Node: verifica la metà Python della catena
di codegen (DTO → ``contracts/openapi.json``). La metà TypeScript
(``web/src/shared/contracts/api.d.ts``) e il confronto file-based completo
sono lo script ``scripts/generate_contracts.py --check``, eseguito dalla CI.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
from newray.modules.conversations import ConversationService
from newray.modules.identity import IdentityService
from newray.modules.profiles import ProfileService

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"


@pytest.fixture()
def identity() -> IdentityService:
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
def profiles() -> ProfileService:
    profiles_repo = InMemoryProfileRepository()
    bindings_store = InMemoryModelBindingStore()
    return ProfileService(
        profiles_repo,
        bindings_store,
        InMemoryModelCatalog(),
        FakeClock(datetime(2026, 9, 16, tzinfo=UTC)),
        InMemoryProfileDefaultsSeeder(profiles_repo, bindings_store),
        default_model_name="llama3.1",
    )


def _spec_text(
    identity: IdentityService, conversations: ConversationService, profiles: ProfileService
) -> str:
    """Stesso rendering deterministico dello script di codegen."""
    spec = create_app(identity, conversations, profiles, cookie_secure=False).openapi()
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_contratto_versionato_coerente_con_dto(
    identity: IdentityService, conversations: ConversationService, profiles: ProfileService
) -> None:
    committed = (CONTRACTS_DIR / "openapi.json").read_text(encoding="utf-8")
    assert _spec_text(identity, conversations, profiles) == committed, (
        "contracts/openapi.json diverge dai DTO: rigenerare con scripts/generate_contracts.py"
    )
