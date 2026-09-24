"""P-07: gateway tool — allowlist, validazione schema, scope iniettato.

Fake deterministiche: nessun DB. Il run store è un doppio minimale che
registra lo scope con cui è stato interrogato, per provare che ``run.status``
risolve sotto lo scope del principal, mai sotto un valore derivato
dall'argomento del modello (NewRay.md §§8.1, 10.3).
"""

from __future__ import annotations

import json
import uuid

from newray.kernel.identity import Principal, Role, Scope
from newray.modules.models import ToolCallRequest
from newray.modules.tools import (
    TOOL_NOT_ALLOWED,
    TOOL_SCHEMA_INVALID,
    RegistryToolGateway,
)
from newray.modules.tools.adapters.run_status import RunStatusTool


class _RecordingStore:
    """Run store minimale: registra lo scope di ogni ``get`` e ritorna un run
    solo se l'id richiesto è quello posseduto dallo scope registrato."""

    def __init__(self, owned: dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID]) -> None:
        # (organization_id, user_id) -> run_id posseduto
        self._owned = owned
        self.scopes: list[Scope] = []

    def get(self, scope: Scope, run_id: uuid.UUID):  # type: ignore[no-untyped-def]
        self.scopes.append(scope)
        owned_run = self._owned.get((scope.organization_id, scope.user_id))
        if owned_run == run_id:
            return _FakeRun(run_id)
        return None


class _FakeRun:
    def __init__(self, run_id: uuid.UUID) -> None:
        self.id = run_id
        self.state = "running"
        self.finish_reason = None
        self.prompt_tokens = None
        self.completion_tokens = None


def _principal() -> Principal:
    return Principal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), Role.OWNER)


def test_toolset_offre_run_status() -> None:
    gateway = RegistryToolGateway((RunStatusTool(_RecordingStore({})),))
    schemas = gateway.tool_schemas(_principal())
    assert [s.name for s in schemas] == ["run.status"]
    assert schemas[0].parameters["required"] == ["run_id"]


def test_tool_sconosciuto_non_eseguito() -> None:
    gateway = RegistryToolGateway((RunStatusTool(_RecordingStore({})),))
    result = gateway.invoke(
        _principal(), ToolCallRequest(call_id="c1", name="mail.send", arguments={})
    )
    assert result.is_error is True
    assert result.error_code == TOOL_NOT_ALLOWED


def test_schema_non_valido_non_esegue() -> None:
    store = _RecordingStore({})
    gateway = RegistryToolGateway((RunStatusTool(store),))
    # Argomento obbligatorio mancante + argomento non previsto.
    result = gateway.invoke(
        _principal(), ToolCallRequest(call_id="c1", name="run.status", arguments={"foo": 1})
    )
    assert result.is_error is True
    assert result.error_code == TOOL_SCHEMA_INVALID
    # Il tool non è mai stato eseguito: nessuna lettura dello store.
    assert store.scopes == []


def test_scope_del_principal_non_dell_argomento() -> None:
    """Il modello fornisce run_id, ma lo scope è quello del principal: un run
    di un altro proprietario risolve a 'non trovato', non a lettura altrui."""
    alice = _principal()
    alice_run = uuid.uuid4()
    store = _RecordingStore({(alice.organization_id, alice.user_id): alice_run})
    gateway = RegistryToolGateway((RunStatusTool(store),))

    # Alice legge il proprio run.
    ok = gateway.invoke(
        alice,
        ToolCallRequest(call_id="c1", name="run.status", arguments={"run_id": str(alice_run)}),
    )
    assert ok.is_error is False
    assert json.loads(ok.content)["found"] is True

    # Bob chiede l'id del run di Alice: lo scope è di Bob → non trovato.
    bob = _principal()
    denied = gateway.invoke(
        bob, ToolCallRequest(call_id="c2", name="run.status", arguments={"run_id": str(alice_run)})
    )
    assert denied.is_error is False
    assert json.loads(denied.content)["found"] is False
    # Lo store è stato interrogato con lo scope di Bob, non con quello di Alice.
    assert store.scopes[-1] == bob.scope


def test_run_id_malformato_e_errore_di_esecuzione() -> None:
    gateway = RegistryToolGateway((RunStatusTool(_RecordingStore({})),))
    result = gateway.invoke(
        _principal(),
        ToolCallRequest(call_id="c1", name="run.status", arguments={"run_id": "non-un-uuid"}),
    )
    assert result.is_error is True
    assert json.loads(result.content)["error"] == "invalid_run_id"
