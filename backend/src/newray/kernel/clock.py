"""Clock iniettabile (NewRay.md §5, kernel/clock.py).

Il dominio non legge l'orologio di sistema: i casi d'uso che scadono o
invecchiano (sessioni, lease, approvazioni) ricevono il tempo da qui, così
i test controllano le scadenze deterministicamente.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Fonte del tempo iniettabile nei casi d'uso."""

    def now(self) -> datetime:
        """Istante corrente, UTC con timezone."""
        ...


class SystemClock:
    """Implementazione di produzione: tempo di sistema in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)
