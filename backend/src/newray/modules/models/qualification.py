"""Prova locale persistente della qualifica di un modello (P-19).

La qualifica è un fatto osservato: una campagna di sonde ha completato con
esito valido su **questo digest** del modello, **questo runtime**, **questo
hardware**. Se una di queste tre coordinate cambia la qualifica decade: non è
più applicabile e va rifatta. Il modulo definisce i tipi di dominio e il
protocollo dello store; l'adapter file-based sta in ``adapters/qualification_file.py``.

Non è un percorso per concedere ``qualified`` senza prova: ``preflight`` e
seed non toccano lo store. Solo lo script di qualifica scrive record dopo aver
osservato che tutte le sonde obbligatorie sono passate (NewRay.md §9.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class HardwareFingerprint:
    """Impronta stabile dell'hardware su cui la qualifica è avvenuta.

    ``gpu_device`` è l'identificatore del device effettivo (es. ``card0``,
    ``card1``): due schede diverse sono hardware diversi. ``gpu_total_bytes``
    e ``cpu_arch`` completano l'ambiente; ulteriori campi possono essere
    aggiunti purché siano derivabili in modo deterministico dall'host.
    """

    gpu_device: str | None
    gpu_total_bytes: int | None
    cpu_arch: str


@dataclass(frozen=True, slots=True)
class QualificationRecord:
    """Prova che ``(model_name, digest, runtime, hardware)`` è stato provato.

    ``qualified_capabilities`` è il sottoinsieme di capacità realmente
    dimostrate dalla campagna di sonde (es. ``chat``, ``vision``, ``tools``);
    ``declared_capabilities`` (dal catalogo runtime) resta separato. Il campo
    ``report_run_id`` collega il record al report JSON della campagna: senza
    quel report la revisione retroattiva non è possibile.

    ``code_hash``/``config_hash``/``corpus_hash`` sono i tre hash di
    riproducibilità: la ripetizione con stesso codice, catalogo e corpus deve
    riprodurre il risultato. Cambiare uno di essi non fa decadere il record
    già emesso: fa decadere la sua applicabilità alla nuova campagna, che
    dovrà emettere il proprio record.
    """

    model_name: str
    digest: str
    runtime: str
    hardware: HardwareFingerprint
    qualified_capabilities: tuple[str, ...]
    report_run_id: str
    code_hash: str
    config_hash: str
    corpus_hash: str
    created_at: datetime
    extra: dict[str, object] = field(default_factory=dict)

    def applies_to(
        self,
        *,
        digest: str,
        runtime: str,
        hardware: HardwareFingerprint,
    ) -> bool:
        """True se questo record copre l'ambiente corrente.

        Non tocca il `code_hash`/`config_hash`/`corpus_hash`: quelli
        distinguono campagne fra loro, ma un record già emesso continua a
        valere finché digest/runtime/hardware coincidono con l'osservato.
        """
        return self.digest == digest and self.runtime == runtime and self.hardware == hardware


class QualificationStore(Protocol):
    """Persistenza della qualifica.

    Lo store è per host, non per utente: la qualifica è uno stato del runtime,
    non un dato privato di un principal (NewRay.md §9.1). Nessuno scope.
    """

    def get(self, model_name: str) -> QualificationRecord | None:
        """Ultimo record noto per il modello, o ``None`` se non esiste."""
        ...

    def record(self, record: QualificationRecord) -> None:
        """Salva il record atomicamente, sovrascrivendo un record precedente."""
        ...

    def invalidate(self, model_name: str) -> None:
        """Rimuove il record, se presente. Idempotente."""
        ...


def qualified_capabilities_for(
    store: QualificationStore,
    *,
    model_name: str,
    digest: str,
    runtime: str,
    hardware: HardwareFingerprint,
) -> tuple[str, ...]:
    """Capacità qualificate per l'ambiente corrente, o ``()`` se decadute.

    Non concede alcuna capacità: se lo store ha un record ma il digest o
    l'hardware sono cambiati, la funzione ritorna ``()`` e chi la usa deve
    riportare l'utente allo stato ``installed_unverified`` (§9.1).
    """
    record = store.get(model_name)
    if record is None:
        return ()
    if not record.applies_to(digest=digest, runtime=runtime, hardware=hardware):
        return ()
    return record.qualified_capabilities


__all__ = [
    "HardwareFingerprint",
    "QualificationRecord",
    "QualificationStore",
    "qualified_capabilities_for",
]
