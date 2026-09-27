"""Compute lease filesystem: `fcntl.flock` esclusivo non-bloccante.

Il lock è per-file, per-host: due processi worker sulla stessa macchina
non possono detenere il lease contemporaneamente. Se il processo detentore
muore, il kernel rilascia il file descriptor e con esso il lock — il
recupero è automatico, senza intervento del DB.

Cross-host: non protetto (per design di questa slice). La serializzazione
multi-host è ambito di P-18 (deployment).
"""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path

logger = logging.getLogger("newray.runs.compute_lease")


class FsComputeLease:
    """Lock avvisorio esclusivo su un file locale."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._fd: int | None = None

    def try_acquire(self) -> bool:
        if self._fd is not None:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        except OSError:
            os.close(fd)
            raise
        self._fd = fd
        logger.debug("compute lease acquisito: %s", self._path)
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            try:
                os.close(self._fd)
            finally:
                self._fd = None
        logger.debug("compute lease rilasciato: %s", self._path)

    @property
    def held(self) -> bool:
        return self._fd is not None
