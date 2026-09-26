"""Orchestrazione della campagna di qualifica (P-19).

Il modulo è puro: nessuna dipendenza da Ollama, GPU o rete. La CLI reale
(``scripts/qualify_local_models.py``) fornisce un :class:`RuntimeProbe`
concreto che parla con Ollama; i test usano un fake che inietta guasti.

La campagna esegue le fasi in ordine (``discovery → warmup → load →
probe``) e chiude sempre con ``unload`` in un blocco try/finally: quando
una fase fallisce, le successive vengono marcate ``skipped`` e ``unload``
viene comunque eseguito per rilasciare le risorse.

Il :class:`QualificationRecord` viene scritto **solo** se tutte le fasi
sono ``passed`` e il probe ha dichiarato ``qualified_capabilities`` non
vuote. Un rapporto ``passed=True`` è condizione necessaria ma non
sufficiente: senza capacità dimostrate lo store non viene toccato.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .qualification import (
    HardwareFingerprint,
    QualificationRecord,
    QualificationStore,
)
from .qualification_report import (
    QualificationReport,
    start_report,
)

#: Fasi che vengono chiuse comunque con ``unload`` alla fine, anche su
#: fallimento delle precedenti (rilascio delle risorse).
_ORDERED_PHASES: tuple[str, ...] = ("discovery", "warmup", "load", "probe")

#: Mappa nome-fase → nome-metodo del probe (non coincidono: la fase si
#: chiama ``probe`` ma il metodo ``probes`` per non urtare la parola
#: chiave riservata degli StrEnum e per leggibilità).
_METHOD_FOR_PHASE: dict[str, str] = {
    "discovery": "discover",
    "warmup": "warmup",
    "load": "load",
    "probe": "probes",
    "unload": "unload",
}


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """Risultato di una singola fase.

    ``details`` finisce nel rapporto sotto ``phases[i].details`` (dizionario
    JSON-serializzabile); ``problems`` è la lista delle stringhe
    problematiche. Se ``status == 'passed'`` deve essere vuota.

    ``digest`` e ``declared_capabilities`` vengono valorizzati dalla fase
    ``discovery``; ``qualified_capabilities`` viene valorizzata dalla fase
    ``probe``. Le altre fasi lasciano questi campi a default.
    """

    status: str  # "passed" | "failed" | "skipped"
    details: dict[str, object] = field(default_factory=dict)
    problems: tuple[str, ...] = ()
    digest: str | None = None
    declared_capabilities: tuple[str, ...] = ()
    qualified_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"passed", "failed", "skipped"}:
            raise ValueError(f"status non ammesso: {self.status!r}")
        if self.status == "passed" and self.problems:
            raise ValueError("status 'passed' non ammette problemi")


class RuntimeProbe(Protocol):
    """Contratto delle sonde della qualifica.

    L'implementazione reale sta accanto alla CLI (adapter Ollama); i test
    offline usano un fake. Ogni metodo può sollevare eccezioni: la
    campagna cattura, registra la fase come ``failed`` con la ragione, e
    prosegue con ``unload``.
    """

    async def discover(self) -> ProbeOutcome:
        """Discovery del modello: presenza, digest, capacità dichiarate."""
        ...

    async def warmup(self) -> ProbeOutcome:
        """Warmup del runtime (caricamento pigro, precondizioni)."""
        ...

    async def load(self) -> ProbeOutcome:
        """Caricamento del modello (misurazione separata dal warmup)."""
        ...

    async def probes(self) -> ProbeOutcome:
        """Sonde funzionali; popola ``qualified_capabilities``."""
        ...

    async def unload(self) -> ProbeOutcome:
        """Scaricamento del modello e rilascio del client."""
        ...


@dataclass(frozen=True, slots=True)
class CampaignConfig:
    """Configurazione fissa della campagna."""

    model_name: str
    output_path: Path
    code_hash: str
    config_hash: str
    corpus_hash: str
    runtime: str
    hardware: HardwareFingerprint | None


@dataclass(frozen=True, slots=True)
class CampaignResult:
    """Esito complessivo, con puntatore al rapporto emesso.

    ``passed`` implica sia il successo di tutte le fasi sia la scrittura di
    un :class:`QualificationRecord`: senza record persistente non c'è
    prova, e senza prova la qualifica non è dichiarata (§9.1).
    ``exit_code`` è pensato per la CLI: 0 su successo, 1 su fallimento
    delle sonde, 2 su errore di configurazione (non prodotto qui — la CLI
    lo emette prima di lanciare la campagna).
    """

    passed: bool
    report_path: Path
    report: QualificationReport
    record: QualificationRecord | None
    exit_code: int


async def run_campaign(
    *,
    config: CampaignConfig,
    probe: RuntimeProbe,
    store: QualificationStore,
) -> CampaignResult:
    """Esegue la campagna e restituisce l'esito.

    - Se una fase fallisce, le successive prima di ``unload`` sono marcate
      ``skipped`` con la ragione; ``unload`` viene sempre tentata.
    - Il :class:`QualificationRecord` viene registrato **solo** su
      successo completo **e** ``qualified_capabilities`` non vuote.
      Su fallimento un eventuale record precedente **non** viene toccato:
      lo store è responsabile della propria revoca esplicita (§P-19).
    """
    writer = start_report(
        config.output_path,
        model_name=config.model_name,
        code_hash=config.code_hash,
        config_hash=config.config_hash,
        corpus_hash=config.corpus_hash,
        runtime=config.runtime,
        hardware=_hardware_dict(config.hardware),
    )
    outcomes: dict[str, ProbeOutcome] = {}
    # ``complete()`` viene chiamato SOLO se la campagna termina in modo
    # ordinato. Un'interruzione (KeyboardInterrupt, SystemExit, cancel)
    # propaga senza toccare ``attempt_completed_at``: il rapporto resta
    # ``passed=False`` come richiesto da §P-19 ("interruzione non
    # distrugge ultimo checkpoint" ma non lo trasforma in successo).
    for phase_name in _ORDERED_PHASES:
        if _any_failed(outcomes):
            outcome = ProbeOutcome(
                status="skipped",
                problems=("fase precedente fallita: campagna interrotta",),
            )
        else:
            outcome = await _run_phase(probe, phase_name)
        writer.finish_phase(
            phase_name,
            status=outcome.status,  # type: ignore[arg-type]
            details=dict(outcome.details),
            problems=list(outcome.problems),
        )
        outcomes[phase_name] = outcome
    # ``unload`` viene sempre eseguito, anche su fallimento delle
    # precedenti (rilascio delle risorse). Le eccezioni non-``Exception``
    # (KeyboardInterrupt, CancelledError, SystemExit) NON sono catturate
    # da ``_run_phase`` e propagano prima dell'``unload``.
    unload = await _run_phase(probe, "unload")
    writer.finish_phase(
        "unload",
        status=unload.status,  # type: ignore[arg-type]
        details=dict(unload.details),
        problems=list(unload.problems),
    )
    outcomes["unload"] = unload
    writer.complete()

    all_passed = all(o.status == "passed" for o in outcomes.values())
    record: QualificationRecord | None = None
    if all_passed:
        discovery = outcomes["discovery"]
        probes_outcome = outcomes["probe"]
        if (
            discovery.digest
            and probes_outcome.qualified_capabilities
            and config.hardware is not None
        ):
            record = QualificationRecord(
                model_name=config.model_name,
                digest=discovery.digest,
                runtime=config.runtime,
                hardware=config.hardware,
                qualified_capabilities=probes_outcome.qualified_capabilities,
                report_run_id=writer.report.run_id,
                code_hash=config.code_hash,
                config_hash=config.config_hash,
                corpus_hash=config.corpus_hash,
                created_at=writer.report.attempt_started_at,
                extra={
                    "declared_capabilities": list(discovery.declared_capabilities),
                },
            )
            store.record(record)
    return CampaignResult(
        passed=all_passed and record is not None,
        report_path=config.output_path,
        report=writer.report,
        record=record,
        exit_code=0 if (all_passed and record is not None) else 1,
    )


async def _run_phase(probe: RuntimeProbe, phase_name: str) -> ProbeOutcome:
    """Esegue una fase e cattura le eccezioni traducendole in ``failed``."""
    method = getattr(probe, _METHOD_FOR_PHASE[phase_name])
    try:
        result = await method()
    except Exception as exc:  # noqa: BLE001
        return ProbeOutcome(
            status="failed",
            problems=(f"{type(exc).__name__}: {exc}",),
        )
    if not isinstance(result, ProbeOutcome):
        return ProbeOutcome(
            status="failed",
            problems=(f"probe.{phase_name}() non ha ritornato ProbeOutcome",),
        )
    return result


def _any_failed(outcomes: dict[str, ProbeOutcome]) -> bool:
    return any(o.status == "failed" for o in outcomes.values())


def _hardware_dict(hardware: HardwareFingerprint | None) -> dict[str, object]:
    if hardware is None:
        return {}
    return {
        "gpu_device": hardware.gpu_device,
        "gpu_total_bytes": hardware.gpu_total_bytes,
        "cpu_arch": hardware.cpu_arch,
    }


__all__ = [
    "CampaignConfig",
    "CampaignResult",
    "ProbeOutcome",
    "RuntimeProbe",
    "run_campaign",
]
