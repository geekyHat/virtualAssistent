"""Rapporto di qualifica progressivo, atomico e non-riusabile (P-19).

Un rapporto ha un ``run_id`` unico, marcatori di ``attempt_started_at`` e
``attempt_completed_at`` separati, e i tre hash di riproducibilità
(``code_hash``, ``config_hash``, ``corpus_hash``). La classe
:class:`QualificationReportWriter` scrive dopo ogni fase in modo atomico
(tempfile + rename) e non "eredita" mai un ``passed`` da un tentativo
precedente: ``load_or_start`` inizia sempre una nuova campagna.

Il modulo è puro: nessun I/O di rete, nessuna dipendenza da Ollama o
dall'adapter. La CLI ``scripts/qualify_local_models.py`` compone questo
writer con le sonde reali.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

#: Fasi previste dalla campagna (ordine cronologico); un rapporto valido le
#: registra tutte, in successo o in fallimento. Il set è chiuso: aggiungere
#: nuove fasi richiede una modifica esplicita di codice e schema.
PHASES: tuple[str, ...] = (
    "discovery",
    "warmup",
    "load",
    "probe",
    "unload",
)

PhaseStatus = Literal["pending", "running", "passed", "failed", "skipped"]


@dataclass
class PhaseRecord:
    """Stato di una fase all'interno di un tentativo.

    ``status`` inizia a ``pending`` e transisce a ``running`` all'inizio,
    quindi a uno stato terminale. Un rapporto ``pending`` o ``running``
    all'atto della lettura significa **interruzione**: non è mai un successo.
    """

    name: str
    status: PhaseStatus = "pending"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    details: dict[str, object] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


@dataclass
class QualificationReport:
    """Rapporto di un unico tentativo di qualifica di un modello.

    ``passed`` è ``True`` solo se **tutte** le fasi obbligatorie sono
    ``passed`` e ``attempt_completed_at`` è valorizzato. Fasi facoltative
    (nessuna nel pilot) potrebbero essere ``skipped``.
    """

    run_id: str
    model_name: str
    schema_version: int
    code_hash: str
    config_hash: str
    corpus_hash: str
    runtime: str | None
    hardware: dict[str, object]
    attempt_started_at: datetime
    attempt_completed_at: datetime | None = None
    phases: dict[str, PhaseRecord] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        if self.attempt_completed_at is None:
            return False
        return all(phase.status == "passed" for phase in self.phases.values())


def compute_code_hash(paths: list[Path]) -> str:
    """Hash SHA-256 dei sorgenti indicati (ordine deterministico).

    Include il path relativo alla prima path (o al file singolo) come
    tag di separazione fra file, così un rinomino cambia l'hash: due
    layout diversi non collidono anche se il contenuto è identico.
    """
    hasher = hashlib.sha256()
    for path in sorted(paths, key=lambda p: str(p)):
        hasher.update(b"---\x00")
        hasher.update(str(path).encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def compute_config_hash(config: dict[str, object]) -> str:
    """Hash SHA-256 di un dict JSON-serializzabile (chiavi ordinate)."""
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_corpus_hash(corpus_dir: Path | None) -> str:
    """Hash SHA-256 dei file del corpus (ordine deterministico).

    ``None`` produce l'hash sentinella ``"no-corpus"``: quel valore va nel
    record come marker esplicito, non come stringa vuota che collidesse
    con "corpus non calcolato".
    """
    if corpus_dir is None:
        return "no-corpus"
    if not corpus_dir.is_dir():
        raise ValueError(f"corpus_dir non è una directory: {corpus_dir}")
    hasher = hashlib.sha256()
    for file in sorted(corpus_dir.rglob("*")):
        if not file.is_file():
            continue
        hasher.update(b"---\x00")
        rel = file.relative_to(corpus_dir).as_posix().encode("utf-8")
        hasher.update(rel)
        hasher.update(b"\x00")
        hasher.update(file.read_bytes())
    return hasher.hexdigest()


class QualificationReportWriter:
    """Scrive un rapporto progressivo su file, in modo atomico.

    Ogni ``flush()`` (o l'uscita da un context manager di fase) riscrive
    l'intero file JSON: tempfile nella stessa dir + rename. Un lettore
    concomitante vede o la versione precedente o la nuova, mai un file a
    metà. La classe non tenta di "riprendere" un rapporto precedente: se
    il file esiste già è di un tentativo diverso.
    """

    def __init__(self, path: Path, report: QualificationReport) -> None:
        if not path.is_absolute():
            raise ValueError("path del rapporto deve essere assoluto")
        self._path = path
        self._report = report

    @property
    def report(self) -> QualificationReport:
        return self._report

    @property
    def path(self) -> Path:
        return self._path

    def start_phase(self, name: str) -> None:
        phase = self._get_or_create(name)
        phase.status = "running"
        phase.started_at = datetime.now(UTC)
        self.flush()

    def finish_phase(
        self,
        name: str,
        *,
        status: PhaseStatus,
        details: dict[str, object] | None = None,
        problems: list[str] | None = None,
    ) -> None:
        phase = self._get_or_create(name)
        if phase.started_at is None:
            phase.started_at = datetime.now(UTC)
        phase.completed_at = datetime.now(UTC)
        phase.status = status
        phase.duration_seconds = round((phase.completed_at - phase.started_at).total_seconds(), 3)
        if details is not None:
            phase.details = details
        if problems is not None:
            phase.problems = list(problems)
        self.flush()

    @contextmanager
    def phase(self, name: str) -> Iterator[PhaseRecord]:
        """Context manager: ``running`` all'entrata, ``failed`` su eccezione."""
        self.start_phase(name)
        phase = self._get_or_create(name)
        try:
            yield phase
        except BaseException as exc:
            # ``failed`` con messaggio derivato dal tipo: nessun leak di
            # payload utente nel rapporto.
            self.finish_phase(
                name,
                status="failed",
                problems=(phase.problems or []) + [f"{type(exc).__name__}: {exc}"],
                details=phase.details,
            )
            raise
        else:
            if phase.status == "running":
                self.finish_phase(
                    name,
                    status="passed",
                    details=phase.details,
                    problems=phase.problems,
                )

    def complete(self) -> None:
        """Segna il tentativo come completato (non necessariamente passato)."""
        self._report.attempt_completed_at = datetime.now(UTC)
        self.flush()

    def flush(self) -> None:
        payload = _encode(self._report)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.", suffix=".tmp", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
            os.replace(tmp_name, self._path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

    def _get_or_create(self, name: str) -> PhaseRecord:
        if name not in PHASES:
            raise ValueError(f"fase sconosciuta: {name!r} (attese: {PHASES})")
        phase = self._report.phases.get(name)
        if phase is None:
            phase = PhaseRecord(name=name)
            self._report.phases[name] = phase
        return phase


def start_report(
    path: Path,
    *,
    model_name: str,
    code_hash: str,
    config_hash: str,
    corpus_hash: str,
    runtime: str | None,
    hardware: dict[str, object],
) -> QualificationReportWriter:
    """Costruisce un nuovo tentativo con ``run_id`` fresco.

    Se ``path`` esiste già viene sovrascritto: il vecchio tentativo non
    è mai portato dentro il nuovo (né via ``passed`` né via campi).
    """
    report = QualificationReport(
        run_id=str(uuid.uuid4()),
        model_name=model_name,
        schema_version=1,
        code_hash=code_hash,
        config_hash=config_hash,
        corpus_hash=corpus_hash,
        runtime=runtime,
        hardware=dict(hardware),
        attempt_started_at=datetime.now(UTC),
        phases={name: PhaseRecord(name=name) for name in PHASES},
    )
    writer = QualificationReportWriter(path, report)
    writer.flush()
    return writer


def load_report(path: Path) -> QualificationReport:
    """Legge un rapporto esistente (per ispezione). Non lo mai fa proseguire.

    Un rapporto letto che risulti ``passed`` è **osservazione storica**: il
    chiamante non può usarlo come esito di una nuova campagna. Per emettere
    di nuovo un successo si deve rilanciare la campagna via ``start_report``.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"rapporto {path.name!r} non leggibile: {type(exc).__name__}") from exc
    return _decode(data)


def _encode(report: QualificationReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "run_id": report.run_id,
        "model_name": report.model_name,
        "code_hash": report.code_hash,
        "config_hash": report.config_hash,
        "corpus_hash": report.corpus_hash,
        "runtime": report.runtime,
        "hardware": report.hardware,
        "attempt_started_at": report.attempt_started_at.isoformat(),
        "attempt_completed_at": (
            report.attempt_completed_at.isoformat()
            if report.attempt_completed_at is not None
            else None
        ),
        "passed": report.passed,
        "phases": [
            {
                "name": phase.name,
                "status": phase.status,
                "started_at": phase.started_at.isoformat() if phase.started_at else None,
                "completed_at": (phase.completed_at.isoformat() if phase.completed_at else None),
                "duration_seconds": phase.duration_seconds,
                "details": phase.details,
                "problems": phase.problems,
            }
            for phase in [report.phases[name] for name in PHASES if name in report.phases]
        ],
    }


def _decode(data: object) -> QualificationReport:
    if not isinstance(data, dict):
        raise ValueError("rapporto: root JSON non è un oggetto")
    if data.get("schema_version") != 1:
        raise ValueError(f"schema_version non supportato: {data.get('schema_version')!r}")
    phases_raw = data.get("phases") or []
    if not isinstance(phases_raw, list):
        raise ValueError("phases deve essere una lista")
    phases: dict[str, PhaseRecord] = {}
    for item in phases_raw:
        if not isinstance(item, dict):
            raise ValueError("elemento phases non è un oggetto")
        name = str(item.get("name"))
        if name not in PHASES:
            raise ValueError(f"fase sconosciuta nel rapporto: {name!r}")
        phases[name] = PhaseRecord(
            name=name,
            status=str(item.get("status") or "pending"),  # type: ignore[arg-type]
            started_at=(
                datetime.fromisoformat(str(item["started_at"])) if item.get("started_at") else None
            ),
            completed_at=(
                datetime.fromisoformat(str(item["completed_at"]))
                if item.get("completed_at")
                else None
            ),
            duration_seconds=(
                float(item["duration_seconds"])
                if item.get("duration_seconds") is not None
                else None
            ),
            details=dict(item.get("details") or {}),
            problems=list(item.get("problems") or []),
        )
    return QualificationReport(
        run_id=str(data["run_id"]),
        model_name=str(data["model_name"]),
        schema_version=1,
        code_hash=str(data["code_hash"]),
        config_hash=str(data["config_hash"]),
        corpus_hash=str(data["corpus_hash"]),
        runtime=(str(data["runtime"]) if data.get("runtime") is not None else None),
        hardware=dict(data.get("hardware") or {}),
        attempt_started_at=datetime.fromisoformat(str(data["attempt_started_at"])),
        attempt_completed_at=(
            datetime.fromisoformat(str(data["attempt_completed_at"]))
            if data.get("attempt_completed_at")
            else None
        ),
        phases=phases,
    )


__all__ = [
    "PHASES",
    "PhaseRecord",
    "PhaseStatus",
    "QualificationReport",
    "QualificationReportWriter",
    "compute_code_hash",
    "compute_config_hash",
    "compute_corpus_hash",
    "load_report",
    "start_report",
]
