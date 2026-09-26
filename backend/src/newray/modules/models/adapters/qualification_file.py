"""Store di qualifica su filesystem: un file JSON per modello (P-19).

Il record vive fuori dal codice sorgente e dai dati privati dell'utente: la
qualifica è uno stato del runtime, non un dato di un principal (§9.1). La
scrittura è atomica (tempfile + rename); la lettura di un file corrotto
solleva ``ValueError`` — mai un successo silenzioso.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from ..qualification import HardwareFingerprint, QualificationRecord

# Regex per un nome file sicuro: solo caratteri innocui, evita traversal.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._:@-]+$")


class FileQualificationStore:
    """Persistenza JSON per file, con nome derivato dal ``model_name``.

    La directory di base va fornita esplicita (di solito
    ``settings.data_dir / "qualifications"``): la costruzione non crea la
    cartella per non nascondere errori di configurazione (§21.2).
    """

    def __init__(self, base_dir: Path) -> None:
        if not base_dir.is_absolute():
            raise ValueError("base_dir della qualifica deve essere assoluto")
        self._base = base_dir

    def _path(self, model_name: str) -> Path:
        if not _SAFE_NAME.fullmatch(model_name):
            raise ValueError(f"model_name non ammesso per il file store: {model_name!r}")
        return self._base / f"{model_name}.json"

    def get(self, model_name: str) -> QualificationRecord | None:
        path = self._path(model_name)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"record di qualifica {model_name!r} corrotto: {type(exc).__name__}"
            ) from exc
        return _decode_record(data, model_name)

    def record(self, record: QualificationRecord) -> None:
        path = self._path(record.model_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _encode_record(record)
        # Scrittura atomica: file temporaneo nella stessa dir → rename.
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=f".{record.model_name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
            os.replace(tmp_name, path)
        except BaseException:
            # Pulizia se rename fallisce; non nascondiamo l'errore.
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

    def invalidate(self, model_name: str) -> None:
        path = self._path(model_name)
        try:
            path.unlink()
        except FileNotFoundError:
            return


def _encode_record(record: QualificationRecord) -> dict[str, object]:
    return {
        "schema_version": 1,
        "model_name": record.model_name,
        "digest": record.digest,
        "runtime": record.runtime,
        "hardware": {
            "gpu_device": record.hardware.gpu_device,
            "gpu_total_bytes": record.hardware.gpu_total_bytes,
            "cpu_arch": record.hardware.cpu_arch,
        },
        "qualified_capabilities": list(record.qualified_capabilities),
        "report_run_id": record.report_run_id,
        "code_hash": record.code_hash,
        "config_hash": record.config_hash,
        "corpus_hash": record.corpus_hash,
        "created_at": record.created_at.isoformat(),
        "extra": dict(record.extra),
    }


def _decode_record(data: object, model_name: str) -> QualificationRecord:
    if not isinstance(data, dict):
        raise ValueError(f"record di qualifica {model_name!r} non è un oggetto JSON")
    if data.get("schema_version") != 1:
        raise ValueError(
            f"schema_version non supportato per {model_name!r}: {data.get('schema_version')!r}"
        )
    if data.get("model_name") != model_name:
        raise ValueError(
            f"model_name del record ({data.get('model_name')!r}) non "
            f"coincide con il nome del file ({model_name!r})"
        )
    hardware_raw = data.get("hardware") or {}
    if not isinstance(hardware_raw, dict):
        raise ValueError("hardware del record non è un oggetto")
    caps = data.get("qualified_capabilities") or []
    if not isinstance(caps, list) or not all(isinstance(c, str) for c in caps):
        raise ValueError("qualified_capabilities deve essere una lista di stringhe")
    return QualificationRecord(
        model_name=str(data["model_name"]),
        digest=str(data["digest"]),
        runtime=str(data["runtime"]),
        hardware=HardwareFingerprint(
            gpu_device=(
                str(hardware_raw["gpu_device"])
                if hardware_raw.get("gpu_device") is not None
                else None
            ),
            gpu_total_bytes=(
                int(hardware_raw["gpu_total_bytes"])
                if hardware_raw.get("gpu_total_bytes") is not None
                else None
            ),
            cpu_arch=str(hardware_raw["cpu_arch"]),
        ),
        qualified_capabilities=tuple(caps),
        report_run_id=str(data["report_run_id"]),
        code_hash=str(data["code_hash"]),
        config_hash=str(data["config_hash"]),
        corpus_hash=str(data["corpus_hash"]),
        created_at=datetime.fromisoformat(str(data["created_at"])),
        extra=dict(data.get("extra") or {}),
    )


__all__ = ["FileQualificationStore"]
