"""P-19: rapporto di qualifica atomico, phase markers, non riusabile."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from newray.modules.models.qualification_report import (
    PHASES,
    compute_code_hash,
    compute_config_hash,
    compute_corpus_hash,
    load_report,
    start_report,
)


def _hw() -> dict[str, object]:
    return {"gpu_device": "card0", "cpu_arch": "x86_64"}


def test_start_report_scrive_fasi_pending(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    writer = start_report(
        path,
        model_name="pilot",
        code_hash="c",
        config_hash="cfg",
        corpus_hash="corp",
        runtime="ollama",
        hardware=_hw(),
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["passed"] is False
    assert data["attempt_completed_at"] is None
    assert [p["name"] for p in data["phases"]] == list(PHASES)
    assert all(p["status"] == "pending" for p in data["phases"])
    # run_id UUID valido, non ereditato da run precedenti.
    assert len(data["run_id"]) == 36
    assert writer.report.run_id == data["run_id"]


def test_phase_context_marca_running_poi_passed(tmp_path: Path) -> None:
    writer = start_report(
        tmp_path / "out.json",
        model_name="pilot",
        code_hash="c",
        config_hash="cfg",
        corpus_hash="corp",
        runtime="ollama",
        hardware=_hw(),
    )
    with writer.phase("discovery") as phase:
        phase.details["found_models"] = 4
    data = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    discovery = next(p for p in data["phases"] if p["name"] == "discovery")
    assert discovery["status"] == "passed"
    assert discovery["details"] == {"found_models": 4}
    assert discovery["started_at"] and discovery["completed_at"]
    assert discovery["duration_seconds"] is not None


def test_phase_context_su_eccezione_marca_failed_e_rilancia(tmp_path: Path) -> None:
    writer = start_report(
        tmp_path / "out.json",
        model_name="pilot",
        code_hash="c",
        config_hash="cfg",
        corpus_hash="corp",
        runtime="ollama",
        hardware=_hw(),
    )
    with pytest.raises(RuntimeError):
        with writer.phase("warmup"):
            raise RuntimeError("no gpu")
    data = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    warmup = next(p for p in data["phases"] if p["name"] == "warmup")
    assert warmup["status"] == "failed"
    assert "RuntimeError: no gpu" in warmup["problems"]
    assert data["passed"] is False


def test_interruzione_conserva_ultimo_checkpoint(tmp_path: Path) -> None:
    """Se la campagna è interrotta prima di ``complete()`` il rapporto
    resta valido come "attempt_completed_at=None" — mai passed=True."""
    path = tmp_path / "out.json"
    writer = start_report(
        path,
        model_name="pilot",
        code_hash="c",
        config_hash="cfg",
        corpus_hash="corp",
        runtime="ollama",
        hardware=_hw(),
    )
    with writer.phase("discovery"):
        pass
    # Simula interruzione: nessuna chiamata a ``complete()``.
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["passed"] is False
    assert data["attempt_completed_at"] is None
    # Le fasi già chiuse restano.
    assert next(p for p in data["phases"] if p["name"] == "discovery")["status"] == "passed"


def test_report_passed_solo_con_tutte_le_fasi_passed_e_complete(tmp_path: Path) -> None:
    writer = start_report(
        tmp_path / "out.json",
        model_name="pilot",
        code_hash="c",
        config_hash="cfg",
        corpus_hash="corp",
        runtime="ollama",
        hardware=_hw(),
    )
    for phase in PHASES:
        with writer.phase(phase):
            pass
    # Non passato finché ``complete()`` non è chiamato.
    assert writer.report.passed is False
    writer.complete()
    assert writer.report.passed is True


def test_load_di_report_precedente_non_e_riusato_per_nuovo_tentativo(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    writer = start_report(
        path,
        model_name="pilot",
        code_hash="c",
        config_hash="cfg",
        corpus_hash="corp",
        runtime="ollama",
        hardware=_hw(),
    )
    for phase in PHASES:
        with writer.phase(phase):
            pass
    writer.complete()
    old_run_id = writer.report.run_id

    # Rileggo: è un tentativo passato, ma solo come osservazione.
    loaded = load_report(path)
    assert loaded.passed is True
    assert loaded.run_id == old_run_id

    # Un nuovo start_report riscrive il file con run_id fresco e passed=False.
    fresh = start_report(
        path,
        model_name="pilot",
        code_hash="c",
        config_hash="cfg",
        corpus_hash="corp",
        runtime="ollama",
        hardware=_hw(),
    )
    assert fresh.report.run_id != old_run_id
    assert fresh.report.passed is False
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == fresh.report.run_id
    assert data["passed"] is False


def test_scrittura_e_atomica_niente_temp_file_residuo(tmp_path: Path) -> None:
    writer = start_report(
        tmp_path / "out.json",
        model_name="pilot",
        code_hash="c",
        config_hash="cfg",
        corpus_hash="corp",
        runtime="ollama",
        hardware=_hw(),
    )
    with writer.phase("discovery"):
        pass
    # Non ci sono file .tmp residui.
    names = [p.name for p in tmp_path.iterdir()]
    assert names == ["out.json"], names


def test_compute_hashes_deterministici(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("uno")
    (tmp_path / "b.txt").write_text("due")
    h1 = compute_corpus_hash(tmp_path)
    h2 = compute_corpus_hash(tmp_path)
    assert h1 == h2
    # Modifica → hash cambia.
    (tmp_path / "a.txt").write_text("altro")
    assert compute_corpus_hash(tmp_path) != h1
    # None → sentinella esplicita.
    assert compute_corpus_hash(None) == "no-corpus"

    # config: chiavi ordinate → hash stabile
    cfg_a = compute_config_hash({"b": 2, "a": 1})
    cfg_b = compute_config_hash({"a": 1, "b": 2})
    assert cfg_a == cfg_b

    # code: path e contenuto entrambi contano
    (tmp_path / "x.py").write_text("print(1)")
    code_a = compute_code_hash([tmp_path / "x.py"])
    (tmp_path / "y.py").write_text("print(1)")
    code_b = compute_code_hash([tmp_path / "y.py"])
    assert code_a != code_b  # path diverso


def test_start_report_rifiuta_path_relativa() -> None:
    with pytest.raises(ValueError):
        start_report(
            Path("rel.json"),
            model_name="pilot",
            code_hash="c",
            config_hash="cfg",
            corpus_hash="corp",
            runtime="ollama",
            hardware=_hw(),
        )


def test_start_phase_su_nome_ignoto_solleva(tmp_path: Path) -> None:
    writer = start_report(
        tmp_path / "out.json",
        model_name="pilot",
        code_hash="c",
        config_hash="cfg",
        corpus_hash="corp",
        runtime="ollama",
        hardware=_hw(),
    )
    with pytest.raises(ValueError):
        writer.start_phase("inesistente")
