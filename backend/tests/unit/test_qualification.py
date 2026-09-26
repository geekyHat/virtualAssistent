"""P-19: prova locale della qualifica, invalidazione e file store atomico."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from newray.modules.models import (
    HardwareFingerprint,
    QualificationRecord,
    qualified_capabilities_for,
)
from newray.modules.models.adapters.qualification_file import FileQualificationStore


def _hw(**overrides: object) -> HardwareFingerprint:
    base = {"gpu_device": "card0", "gpu_total_bytes": 24 * 2**30, "cpu_arch": "x86_64"}
    base.update(overrides)
    return HardwareFingerprint(**base)  # type: ignore[arg-type]


def _record(**overrides: object) -> QualificationRecord:
    now = datetime.now(UTC)
    fields = {
        "model_name": "newray-gemma4-31b-it",
        "digest": "sha256:aa",
        "runtime": "ollama",
        "hardware": _hw(),
        "qualified_capabilities": ("chat", "tools"),
        "report_run_id": "run-1",
        "code_hash": "code-1",
        "config_hash": "cfg-1",
        "corpus_hash": "corpus-1",
        "created_at": now,
        "extra": {},
    }
    fields.update(overrides)
    return QualificationRecord(**fields)  # type: ignore[arg-type]


def test_applies_to_richiede_digest_runtime_hardware_uguali() -> None:
    r = _record()
    hw = _hw()
    assert r.applies_to(digest="sha256:aa", runtime="ollama", hardware=hw) is True
    # digest diverso → decade
    assert r.applies_to(digest="sha256:bb", runtime="ollama", hardware=hw) is False
    # runtime diverso → decade
    assert r.applies_to(digest="sha256:aa", runtime="llama.cpp", hardware=hw) is False
    # gpu device diverso → decade
    assert (
        r.applies_to(digest="sha256:aa", runtime="ollama", hardware=_hw(gpu_device="card1"))
        is False
    )


def test_qualified_capabilities_ritorna_vuoto_su_mismatch(tmp_path: Path) -> None:
    store = FileQualificationStore(tmp_path)
    store.record(_record())
    hw = _hw()
    # Match esatto: capacità qualificate visibili.
    caps = qualified_capabilities_for(
        store,
        model_name="newray-gemma4-31b-it",
        digest="sha256:aa",
        runtime="ollama",
        hardware=hw,
    )
    assert caps == ("chat", "tools")
    # Digest diverso → capacità decadute.
    caps = qualified_capabilities_for(
        store,
        model_name="newray-gemma4-31b-it",
        digest="sha256:new",
        runtime="ollama",
        hardware=hw,
    )
    assert caps == ()
    # Modello sconosciuto → capacità vuote, non solleva.
    caps = qualified_capabilities_for(
        store,
        model_name="ignoto",
        digest="sha256:zz",
        runtime="ollama",
        hardware=hw,
    )
    assert caps == ()


def test_file_store_scrittura_atomica_e_roundtrip(tmp_path: Path) -> None:
    store = FileQualificationStore(tmp_path)
    r = _record()
    store.record(r)
    # Il file su disco è JSON schema_version 1 con created_at ISO.
    files = list(tmp_path.iterdir())
    # Nessun file temporaneo residuo.
    assert all(not f.name.startswith(".") for f in files)
    written = json.loads(files[0].read_text(encoding="utf-8"))
    assert written["schema_version"] == 1
    assert written["model_name"] == r.model_name
    assert written["qualified_capabilities"] == ["chat", "tools"]
    # Roundtrip.
    loaded = store.get(r.model_name)
    assert loaded == r


def test_file_store_get_su_file_corrotto_solleva(tmp_path: Path) -> None:
    store = FileQualificationStore(tmp_path)
    path = tmp_path / "corrotto.json"
    path.write_text("{ non json", encoding="utf-8")
    with pytest.raises(ValueError):
        store.get("corrotto")


def test_file_store_get_su_schema_version_ignoto_solleva(tmp_path: Path) -> None:
    store = FileQualificationStore(tmp_path)
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"schema_version": 99, "model_name": "future"}), encoding="utf-8")
    with pytest.raises(ValueError):
        store.get("future")


def test_file_store_invalidate_e_idempotente(tmp_path: Path) -> None:
    store = FileQualificationStore(tmp_path)
    store.record(_record())
    assert store.get("newray-gemma4-31b-it") is not None
    store.invalidate("newray-gemma4-31b-it")
    assert store.get("newray-gemma4-31b-it") is None
    # Idempotente: seconda invalidate non solleva.
    store.invalidate("newray-gemma4-31b-it")


def test_file_store_rifiuta_model_name_pericoloso(tmp_path: Path) -> None:
    store = FileQualificationStore(tmp_path)
    with pytest.raises(ValueError):
        store.record(_record(model_name="../evasione"))
    with pytest.raises(ValueError):
        store.get("../evasione")
    with pytest.raises(ValueError):
        store.invalidate("evasione/con/slash")


def test_file_store_rifiuta_base_dir_relativa() -> None:
    with pytest.raises(ValueError):
        FileQualificationStore(Path("relativa"))


def test_get_su_file_con_model_name_mismatch_solleva(tmp_path: Path) -> None:
    """Difesa in profondità: file rinominato non deve passare come valido."""
    store = FileQualificationStore(tmp_path)
    store.record(_record(model_name="modello-A"))
    # Rinomina manuale il file: il contenuto dice modello-A, il nome dice B.
    (tmp_path / "modello-A.json").rename(tmp_path / "modello-B.json")
    with pytest.raises(ValueError):
        store.get("modello-B")
