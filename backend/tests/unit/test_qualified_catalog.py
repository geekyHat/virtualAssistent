"""P-19: wrapper QualifiedModelCatalog che non concede QUALIFIED implicito."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from newray.modules.models import (
    HardwareFingerprint,
    ModelInfo,
    ModelReadiness,
    ModelStatus,
    QualificationRecord,
    QualificationStore,
    ReadinessState,
)
from newray.modules.models.adapters.qualified_catalog import QualifiedModelCatalog


class _InMemoryStore:
    def __init__(self) -> None:
        self._records: dict[str, QualificationRecord] = {}

    def get(self, model_name: str) -> QualificationRecord | None:
        return self._records.get(model_name)

    def record(self, record: QualificationRecord) -> None:
        self._records[record.model_name] = record

    def invalidate(self, model_name: str) -> None:
        self._records.pop(model_name, None)


class _StubCatalog:
    def __init__(self, models: list[ModelInfo], readiness: ModelReadiness) -> None:
        self._models = models
        self._readiness = readiness

    async def list_models(self) -> list[ModelInfo]:
        return list(self._models)

    async def readiness(self, model_name: str | None) -> ModelReadiness:
        return self._readiness


def _hw() -> HardwareFingerprint:
    return HardwareFingerprint(gpu_device="card0", gpu_total_bytes=None, cpu_arch="x86_64")


def _record() -> QualificationRecord:
    return QualificationRecord(
        model_name="pilot",
        digest="sha256:aa",
        runtime="ollama",
        hardware=_hw(),
        qualified_capabilities=("chat", "tools"),
        report_run_id="r1",
        code_hash="c",
        config_hash="cfg",
        corpus_hash="corp",
        created_at=datetime.now(UTC),
    )


def test_readiness_espone_qualified_solo_su_match_completo() -> None:
    store: QualificationStore = _InMemoryStore()  # type: ignore[assignment]
    store.record(_record())
    base = ModelReadiness(
        state=ReadinessState.INSTALLED_UNVERIFIED,
        model_name="pilot",
        digest="sha256:aa",
        declared_capabilities=("chat", "tools", "vision"),
    )
    catalog = QualifiedModelCatalog(_StubCatalog([], base), store, hardware=_hw(), runtime="ollama")
    result = asyncio.run(catalog.readiness("pilot"))
    # Lo state resta INSTALLED_UNVERIFIED: il wrapper non promuove.
    assert result.state == ReadinessState.INSTALLED_UNVERIFIED
    assert result.qualified_capabilities == ("chat", "tools")
    assert result.declared_capabilities == ("chat", "tools", "vision")


def test_readiness_su_digest_diverso_non_espone_qualified() -> None:
    store: QualificationStore = _InMemoryStore()  # type: ignore[assignment]
    store.record(_record())
    base = ModelReadiness(
        state=ReadinessState.INSTALLED_UNVERIFIED,
        model_name="pilot",
        digest="sha256:bb",  # diverso dal record
        declared_capabilities=("chat",),
    )
    catalog = QualifiedModelCatalog(_StubCatalog([], base), store, hardware=_hw(), runtime="ollama")
    result = asyncio.run(catalog.readiness("pilot"))
    assert result.qualified_capabilities == ()


def test_list_models_sostituisce_capabilities_ma_non_lo_status() -> None:
    store: QualificationStore = _InMemoryStore()  # type: ignore[assignment]
    store.record(_record())
    models = [
        ModelInfo(
            name="pilot",
            runtime="ollama",
            digest="sha256:aa",
            status=ModelStatus.INSTALLED,
            capabilities=("chat", "tools", "vision"),
        ),
        ModelInfo(
            name="altro",
            runtime="ollama",
            digest="sha256:zz",
            status=ModelStatus.INSTALLED,
            capabilities=("chat",),
        ),
    ]
    catalog = QualifiedModelCatalog(
        _StubCatalog(models, ModelReadiness(ReadinessState.CATALOG_EMPTY, None)),
        store,
        hardware=_hw(),
        runtime="ollama",
    )
    result = asyncio.run(catalog.list_models())
    pilot = next(m for m in result if m.name == "pilot")
    # Le capacità visibili sono quelle qualificate (chat, tools) — non vision.
    assert pilot.capabilities == ("chat", "tools")
    assert pilot.status == ModelStatus.INSTALLED  # status NON promosso a qualified
    altro = next(m for m in result if m.name == "altro")
    assert altro.capabilities == ("chat",)  # nessuna qualifica → dichiarate


def test_hardware_none_disabilita_wrapper() -> None:
    store: QualificationStore = _InMemoryStore()  # type: ignore[assignment]
    store.record(_record())
    base = ModelReadiness(
        state=ReadinessState.INSTALLED_UNVERIFIED,
        model_name="pilot",
        digest="sha256:aa",
        declared_capabilities=("chat",),
    )
    catalog = QualifiedModelCatalog(_StubCatalog([], base), store, hardware=None, runtime="ollama")
    result = asyncio.run(catalog.readiness("pilot"))
    assert result.qualified_capabilities == ()
