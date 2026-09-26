"""P-19: orchestrazione campagna con fault-injection su ogni fase.

Nessuna GPU, nessuna rete: :class:`FakeRuntimeProbe` inietta esiti/eccezioni
a piacere. Verifica:

- fase in errore → JSON valido con phase.status ``failed`` e successive
  marcate ``skipped``; ``unload`` sempre eseguito;
- exit_code non zero;
- il record di qualifica **non** viene scritto se una fase fallisce;
- il record viene scritto **solo** su successo completo + capacità
  qualificate non vuote;
- test eseguibile da CWD diversa (path assoluta);
- eccezione grezza nel probe è catturata come ``failed`` non propagata.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from newray.modules.models import (
    CampaignConfig,
    HardwareFingerprint,
    ProbeOutcome,
    QualificationRecord,
    run_campaign,
)
from newray.modules.models.adapters.qualification_file import FileQualificationStore


def _hw() -> HardwareFingerprint:
    return HardwareFingerprint(gpu_device="card0", gpu_total_bytes=24 * 2**30, cpu_arch="x86_64")


def _config(tmp_path: Path, output: str = "report.json") -> CampaignConfig:
    return CampaignConfig(
        model_name="pilot",
        output_path=tmp_path / output,
        code_hash="code-h",
        config_hash="config-h",
        corpus_hash="corpus-h",
        runtime="ollama",
        hardware=_hw(),
    )


@dataclass
class FakeRuntimeProbe:
    """Probe con esiti configurabili per fase o eccezioni iniettabili.

    Ogni attributo può essere un :class:`ProbeOutcome` o un ``Callable`` che
    solleva un'eccezione: la campagna la cattura e traduce in ``failed``.
    """

    discover_outcome: ProbeOutcome | Callable[[], ProbeOutcome] = field(
        default_factory=lambda: ProbeOutcome(
            status="passed",
            digest="sha256:aa",
            declared_capabilities=("chat", "tools", "vision"),
            details={"note": "found"},
        )
    )
    warmup_outcome: ProbeOutcome | Callable[[], ProbeOutcome] = field(
        default_factory=lambda: ProbeOutcome(status="passed")
    )
    load_outcome: ProbeOutcome | Callable[[], ProbeOutcome] = field(
        default_factory=lambda: ProbeOutcome(status="passed")
    )
    probes_outcome: ProbeOutcome | Callable[[], ProbeOutcome] = field(
        default_factory=lambda: ProbeOutcome(
            status="passed",
            qualified_capabilities=("chat", "tools"),
        )
    )
    unload_outcome: ProbeOutcome | Callable[[], ProbeOutcome] = field(
        default_factory=lambda: ProbeOutcome(status="passed")
    )
    calls: list[str] = field(default_factory=list)

    async def discover(self) -> ProbeOutcome:
        self.calls.append("discover")
        return _resolve(self.discover_outcome)

    async def warmup(self) -> ProbeOutcome:
        self.calls.append("warmup")
        return _resolve(self.warmup_outcome)

    async def load(self) -> ProbeOutcome:
        self.calls.append("load")
        return _resolve(self.load_outcome)

    async def probes(self) -> ProbeOutcome:
        self.calls.append("probes")
        return _resolve(self.probes_outcome)

    async def unload(self) -> ProbeOutcome:
        self.calls.append("unload")
        return _resolve(self.unload_outcome)


def _resolve(value):
    if callable(value):
        return value()
    return value


def _phase(report: dict, name: str) -> dict:
    return next(p for p in report["phases"] if p["name"] == name)


def test_success_scrive_record_e_exit_zero(tmp_path: Path) -> None:
    store = FileQualificationStore(tmp_path)
    probe = FakeRuntimeProbe()
    result = asyncio.run(run_campaign(config=_config(tmp_path), probe=probe, store=store))
    assert result.passed is True
    assert result.exit_code == 0
    assert result.record is not None
    assert result.record.qualified_capabilities == ("chat", "tools")
    assert result.record.digest == "sha256:aa"
    assert result.record.report_run_id == result.report.run_id
    # Record persistito.
    persisted = store.get("pilot")
    assert persisted is not None
    assert persisted == result.record
    assert probe.calls == ["discover", "warmup", "load", "probes", "unload"]
    # Rapporto valido su disco.
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert data["passed"] is True
    for phase in ["discovery", "warmup", "load", "probe", "unload"]:
        assert _phase(data, phase)["status"] == "passed"


@pytest.mark.parametrize(
    ("failing_phase", "probe_field"),
    [
        ("discovery", "discover_outcome"),
        ("warmup", "warmup_outcome"),
        ("load", "load_outcome"),
        ("probe", "probes_outcome"),
    ],
)
def test_fault_injection_ogni_fase_produce_report_valido_e_exit_nonzero(
    tmp_path: Path, failing_phase: str, probe_field: str
) -> None:
    store = FileQualificationStore(tmp_path)
    kwargs = {probe_field: ProbeOutcome(status="failed", problems=("guasto sim",))}
    probe = FakeRuntimeProbe(**kwargs)  # type: ignore[arg-type]
    result = asyncio.run(run_campaign(config=_config(tmp_path), probe=probe, store=store))
    assert result.passed is False
    assert result.exit_code == 1
    assert result.record is None
    # Nessun record scritto.
    assert store.get("pilot") is None

    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert data["passed"] is False
    assert _phase(data, failing_phase)["status"] == "failed"
    # Le fasi successive sono skipped (ma unload viene comunque tentato).
    order = ["discovery", "warmup", "load", "probe"]
    idx = order.index(failing_phase)
    for skipped in order[idx + 1 :]:
        assert _phase(data, skipped)["status"] == "skipped"
    # Unload sempre eseguito.
    assert "unload" in probe.calls
    assert _phase(data, "unload")["status"] == "passed"


def test_eccezione_nel_probe_e_catturata_come_failed(tmp_path: Path) -> None:
    store = FileQualificationStore(tmp_path)

    def boom() -> ProbeOutcome:
        raise RuntimeError("driver morto")

    probe = FakeRuntimeProbe(load_outcome=boom)
    result = asyncio.run(run_campaign(config=_config(tmp_path), probe=probe, store=store))
    assert result.passed is False
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    load = _phase(data, "load")
    assert load["status"] == "failed"
    assert any("RuntimeError: driver morto" in p for p in load["problems"])
    assert store.get("pilot") is None


def test_probe_senza_capabilities_qualificate_non_scrive_record(tmp_path: Path) -> None:
    """Un rapporto ``passed=True`` ma senza capabilities dimostrate non
    autoconcede la qualifica: nessun record, nessun rilascio."""
    store = FileQualificationStore(tmp_path)
    probe = FakeRuntimeProbe(
        probes_outcome=ProbeOutcome(status="passed", qualified_capabilities=())
    )
    result = asyncio.run(run_campaign(config=_config(tmp_path), probe=probe, store=store))
    assert result.passed is False
    assert result.record is None
    assert store.get("pilot") is None
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    # Il rapporto delle fasi resta passed: la campagna, no.
    assert data["passed"] is True
    assert result.exit_code == 1


def test_hardware_none_non_produce_record(tmp_path: Path) -> None:
    store = FileQualificationStore(tmp_path)
    config = CampaignConfig(
        model_name="pilot",
        output_path=tmp_path / "report.json",
        code_hash="c",
        config_hash="cfg",
        corpus_hash="corp",
        runtime="ollama",
        hardware=None,
    )
    probe = FakeRuntimeProbe()
    result = asyncio.run(run_campaign(config=config, probe=probe, store=store))
    assert result.record is None
    assert result.passed is False


def test_unload_sempre_eseguito_anche_su_fallimento_discovery(tmp_path: Path) -> None:
    store = FileQualificationStore(tmp_path)
    probe = FakeRuntimeProbe(discover_outcome=ProbeOutcome(status="failed", problems=("missing",)))
    result = asyncio.run(run_campaign(config=_config(tmp_path), probe=probe, store=store))
    assert result.passed is False
    assert "unload" in probe.calls
    # Ma anche discover è stato chiamato.
    assert probe.calls[0] == "discover"


def test_campagna_da_cwd_diversa_usa_path_assoluto(tmp_path: Path) -> None:
    """Cambio CWD durante la campagna non deve rompere le scritture."""
    original_cwd = Path.cwd()
    other_dir = tmp_path / "altrove"
    other_dir.mkdir()
    output = tmp_path / "report.json"
    store = FileQualificationStore(tmp_path / "store")
    (tmp_path / "store").mkdir()
    os.chdir(other_dir)
    try:
        result = asyncio.run(
            run_campaign(
                config=CampaignConfig(
                    model_name="pilot",
                    output_path=output,
                    code_hash="c",
                    config_hash="cfg",
                    corpus_hash="corp",
                    runtime="ollama",
                    hardware=_hw(),
                ),
                probe=FakeRuntimeProbe(),
                store=store,
            )
        )
    finally:
        os.chdir(original_cwd)
    assert result.passed is True
    assert output.is_file()
    assert store.get("pilot") is not None


def test_interruzione_lascia_rapporto_incompleto_e_nessun_record(tmp_path: Path) -> None:
    """Se il probe solleva ``KeyboardInterrupt``/``asyncio.CancelledError``,
    il rapporto resta con ``passed=False`` e ``attempt_completed_at`` NON
    valorizzato quando la campagna esce prima del ``writer.complete()``."""
    store = FileQualificationStore(tmp_path)

    class _Cancelling:
        async def discover(self) -> ProbeOutcome:
            return ProbeOutcome(
                status="passed",
                digest="sha256:aa",
                declared_capabilities=("chat",),
            )

        async def warmup(self) -> ProbeOutcome:
            raise KeyboardInterrupt("user")

        async def load(self) -> ProbeOutcome:  # pragma: no cover
            return ProbeOutcome(status="passed")

        async def probes(self) -> ProbeOutcome:  # pragma: no cover
            return ProbeOutcome(status="passed")

        async def unload(self) -> ProbeOutcome:  # pragma: no cover
            return ProbeOutcome(status="passed")

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(run_campaign(config=_config(tmp_path), probe=_Cancelling(), store=store))
    # Il rapporto è stato scritto fino a discovery incluso; complete()
    # non è stato chiamato → attempt_completed_at è None → passed False.
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert data["attempt_completed_at"] is None
    assert data["passed"] is False
    # Discovery è stata registrata come passed prima dell'interruzione.
    assert _phase(data, "discovery")["status"] == "passed"
    # Nessun record.
    assert store.get("pilot") is None


def test_probe_ritorna_valore_non_probe_outcome_e_failed(tmp_path: Path) -> None:
    class Broken:
        async def discover(self):
            return "non è un ProbeOutcome"

        async def warmup(self):
            return ProbeOutcome(status="passed")

        async def load(self):
            return ProbeOutcome(status="passed")

        async def probes(self):
            return ProbeOutcome(status="passed", qualified_capabilities=("chat",))

        async def unload(self):
            return ProbeOutcome(status="passed")

    store = FileQualificationStore(tmp_path)
    result = asyncio.run(run_campaign(config=_config(tmp_path), probe=Broken(), store=store))
    assert result.passed is False
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    disc = _phase(data, "discovery")
    assert disc["status"] == "failed"
    assert any("ProbeOutcome" in p for p in disc["problems"])


def test_record_extra_conserva_declared_capabilities(tmp_path: Path) -> None:
    store = FileQualificationStore(tmp_path)
    probe = FakeRuntimeProbe(
        discover_outcome=ProbeOutcome(
            status="passed",
            digest="sha256:aa",
            declared_capabilities=("chat", "tools", "vision", "insights"),
        ),
        probes_outcome=ProbeOutcome(status="passed", qualified_capabilities=("chat", "tools")),
    )
    result = asyncio.run(run_campaign(config=_config(tmp_path), probe=probe, store=store))
    assert isinstance(result.record, QualificationRecord)
    assert result.record.extra["declared_capabilities"] == [
        "chat",
        "tools",
        "vision",
        "insights",
    ]
    # Roundtrip file: extra sopravvive.
    reloaded = store.get("pilot")
    assert reloaded is not None
    assert reloaded.extra["declared_capabilities"] == list(
        result.record.extra["declared_capabilities"]
    )


def test_created_at_del_record_uguale_a_attempt_started(tmp_path: Path) -> None:
    """Il record è ancorato all'istante di inizio della campagna, non a now."""
    store = FileQualificationStore(tmp_path)
    result = asyncio.run(
        run_campaign(config=_config(tmp_path), probe=FakeRuntimeProbe(), store=store)
    )
    assert result.record is not None
    assert result.record.created_at == result.report.attempt_started_at
    # created_at recente (< 5s fa)
    delta = (datetime.now(UTC) - result.record.created_at).total_seconds()
    assert 0 <= delta < 5


def test_fallimento_dopo_record_precedente_non_lo_invalida(tmp_path: Path) -> None:
    """La revoca è responsabilità esplicita: un rapporto fallito non
    invalida un record esistente automaticamente."""
    store = FileQualificationStore(tmp_path)
    # Prima campagna: successo → record scritto.
    ok = asyncio.run(run_campaign(config=_config(tmp_path), probe=FakeRuntimeProbe(), store=store))
    assert ok.passed
    assert store.get("pilot") is not None
    old_record = store.get("pilot")

    # Seconda campagna: fallisce.
    probe = FakeRuntimeProbe(
        probes_outcome=ProbeOutcome(status="failed", problems=("regressione",))
    )
    fail = asyncio.run(
        run_campaign(
            config=_config(tmp_path, output="report2.json"),
            probe=probe,
            store=store,
        )
    )
    assert not fail.passed
    # Il record precedente NON è stato toccato.
    current = store.get("pilot")
    assert current == old_record
