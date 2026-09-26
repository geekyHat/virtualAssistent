"""P-07: schema, allowlist, tool `run_status` con scope server-side."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from newray.kernel.errors import NotFound
from newray.kernel.identity import Principal, Role
from newray.modules.tools import (
    ToolCall,
    ToolInvocationState,
    ToolRegistry,
    ToolSchema,
    ToolSchemaError,
    ToolUnknownError,
)
from newray.modules.tools.adapters.run_status import RunStatusTool


def _principal() -> Principal:
    return Principal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), Role.OWNER)


class _FakeRuns:
    def __init__(self, run: object | None = None) -> None:
        self._run = run

    async def get(self, principal: Principal, run_id: uuid.UUID):  # noqa: ARG002
        if self._run is None:
            raise NotFound("run non trovato")
        return self._run


class _FakeRun:
    def __init__(self, **overrides: object) -> None:
        self.id = overrides.get("id", uuid.uuid4())
        self.state = _State(overrides.get("state", "completed"))
        self.finish_reason = overrides.get("finish_reason", "stop")
        self.prompt_tokens = overrides.get("prompt_tokens", 3)
        self.completion_tokens = overrides.get("completion_tokens", 7)
        self.eval_duration_ns = overrides.get("eval_duration_ns", 1_000_000)
        self.cancel_requested_at = overrides.get("cancel_requested_at", None)


class _State:
    def __init__(self, v: str) -> None:
        self.value = v


# Schema -----------------------------------------------------------------


def test_schema_rifiuta_argomento_non_dichiarato() -> None:
    schema = ToolSchema(
        properties={"query": {"type": "string"}},
        required=("query",),
    )
    with pytest.raises(ToolSchemaError):
        schema.validate({"query": "ok", "extra": "boh"})


def test_schema_rifiuta_argomento_richiesto_mancante() -> None:
    schema = ToolSchema(
        properties={"query": {"type": "string"}},
        required=("query",),
    )
    with pytest.raises(ToolSchemaError):
        schema.validate({})


def test_schema_valida_tipi() -> None:
    schema = ToolSchema(
        properties={
            "s": {"type": "string"},
            "n": {"type": "integer"},
            "f": {"type": "number"},
            "b": {"type": "boolean"},
        },
    )
    schema.validate({"s": "x", "n": 1, "f": 1.5, "b": True})
    with pytest.raises(ToolSchemaError):
        schema.validate({"s": 1})
    with pytest.raises(ToolSchemaError):
        # bool NON è valido per integer.
        schema.validate({"n": True})
    with pytest.raises(ToolSchemaError):
        schema.validate({"b": "true"})


def test_schema_enum_rispettato() -> None:
    schema = ToolSchema(
        properties={"mode": {"type": "string", "enum": ["read", "write"]}},
    )
    schema.validate({"mode": "read"})
    with pytest.raises(ToolSchemaError):
        schema.validate({"mode": "delete"})


# Registry ---------------------------------------------------------------


def test_registry_rifiuta_tool_ignoto() -> None:
    registry = ToolRegistry(tools={})
    with pytest.raises(ToolUnknownError):
        registry.get("run_status")
    with pytest.raises(ToolUnknownError):
        registry.validate_call(
            ToolCall(call_id="c1", tool_name="run_status", arguments={"run_id": "x"})
        )


def test_registry_validate_call_verifica_schema() -> None:
    tool = RunStatusTool(_FakeRuns(_FakeRun()))  # type: ignore[arg-type]
    registry = ToolRegistry(tools={tool.descriptor.name: tool})
    # OK
    registry.validate_call(
        ToolCall(
            call_id="c1",
            tool_name="run_status",
            arguments={"run_id": str(uuid.uuid4())},
        )
    )
    # arg mancante
    with pytest.raises(ToolSchemaError):
        registry.validate_call(ToolCall(call_id="c1", tool_name="run_status", arguments={}))
    # tipo errato
    with pytest.raises(ToolSchemaError):
        registry.validate_call(
            ToolCall(
                call_id="c1",
                tool_name="run_status",
                arguments={"run_id": 42},
            )
        )


def test_registry_descriptors_espone_solo_lista_allowlist() -> None:
    tool = RunStatusTool(_FakeRuns(_FakeRun()))  # type: ignore[arg-type]
    registry = ToolRegistry(tools={tool.descriptor.name: tool})
    descriptors = registry.descriptors()
    assert [d.name for d in descriptors] == ["run_status"]


# ToolCall / ToolResult --------------------------------------------------


def test_toolcall_rifiuta_valori_non_json_serializzabili() -> None:
    with pytest.raises(ValueError):
        ToolCall(
            call_id="c1",
            tool_name="run_status",
            arguments={"obj": object()},  # type: ignore[dict-item]
        )


def test_toolcall_freezes_arguments() -> None:
    call = ToolCall(
        call_id="c1",
        tool_name="run_status",
        arguments={"run_id": "abc", "nested": {"a": 1}},
    )
    with pytest.raises(TypeError):
        call.arguments["run_id"] = "furto"  # type: ignore[index]


# RunStatusTool ----------------------------------------------------------


def test_run_status_ritorna_snapshot_scoped() -> None:
    principal = _principal()
    run = _FakeRun(state="completed", finish_reason="stop")
    tool = RunStatusTool(_FakeRuns(run))  # type: ignore[arg-type]
    result = asyncio.run(
        tool.execute(
            principal,
            ToolCall(
                call_id="c1",
                tool_name="run_status",
                arguments={"run_id": str(run.id)},
            ),
        )
    )
    assert result.state == ToolInvocationState.SUCCEEDED
    assert result.payload["state"] == "completed"
    assert result.payload["run_id"] == str(run.id)
    assert result.payload["cancel_requested"] is False


def test_run_status_run_id_non_uuid_e_failed_non_exception() -> None:
    principal = _principal()
    tool = RunStatusTool(_FakeRuns(_FakeRun()))  # type: ignore[arg-type]
    result = asyncio.run(
        tool.execute(
            principal,
            ToolCall(
                call_id="c1",
                tool_name="run_status",
                arguments={"run_id": "non-un-uuid"},
            ),
        )
    )
    assert result.state == ToolInvocationState.FAILED
    assert result.payload == {}
    assert any("UUID" in p for p in result.problems)


def test_run_status_run_out_of_scope_e_failed_non_leaked() -> None:
    """`NotFound` (scope estraneo o id inesistente) → failed uniforme.
    Il tool non deve rivelare la differenza tra "non esiste" e
    "esiste ma di un altro utente"."""
    principal = _principal()
    tool = RunStatusTool(_FakeRuns(run=None))  # type: ignore[arg-type]
    result = asyncio.run(
        tool.execute(
            principal,
            ToolCall(
                call_id="c1",
                tool_name="run_status",
                arguments={"run_id": str(uuid.uuid4())},
            ),
        )
    )
    assert result.state == ToolInvocationState.FAILED
    assert result.payload == {}
    # Il messaggio è generico: nessuna informazione sull'altro utente.
    assert result.problems == ("run non trovato nello scope corrente",)


def test_toolresult_succeeded_non_ammette_problems() -> None:
    from newray.modules.tools import ToolResult

    with pytest.raises(ValueError):
        ToolResult(
            call_id="c1",
            tool_name="run_status",
            state=ToolInvocationState.SUCCEEDED,
            payload={},
            problems=("residuo",),
        )


def test_toolresult_state_non_terminale_rifiutato() -> None:
    from newray.modules.tools import ToolResult

    with pytest.raises(ValueError):
        ToolResult(
            call_id="c1",
            tool_name="run_status",
            state=ToolInvocationState.PREPARED,
            payload={},
        )
