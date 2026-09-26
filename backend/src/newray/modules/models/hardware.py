"""Discovery dell'hardware locale per la qualifica (P-19).

Legge solo file di sistema (``/sys/class/drm``) e ``platform``: nessuna
rete, nessuna sonda del driver, nessuna promessa di completezza. Su
ambienti senza GPU visibile ``discover_hardware`` ritorna ``None``: il
wrapper della qualifica sa non produrre risultati di conseguenza.

Il fingerprint identifica **il device fisico** (es. ``card0``), non
"una GPU generica": due schede diverse sono ambienti diversi.
"""

from __future__ import annotations

import platform
from pathlib import Path

from .qualification import HardwareFingerprint


def discover_hardware(root: Path = Path("/sys/class/drm")) -> HardwareFingerprint | None:
    """Fingerprint dell'ambiente corrente, o ``None`` se non determinabile.

    Sceglie il primo ``card*`` con ``mem_info_vram_total`` leggibile: se ne
    esistono due (multi-GPU) e vogliamo distinguere, il chiamante può
    passare ``root`` diverso o comporre il fingerprint a mano.
    """
    cpu_arch = platform.machine() or "unknown"
    if not root.is_dir():
        return HardwareFingerprint(gpu_device=None, gpu_total_bytes=None, cpu_arch=cpu_arch)
    for device in sorted(root.glob("card*/device")):
        total = device / "mem_info_vram_total"
        if not total.is_file():
            continue
        try:
            gpu_total_bytes = int(total.read_text())
        except (OSError, ValueError):
            continue
        # ``device.parent.name`` è ``cardN`` — l'identificatore stabile del
        # link nel sysfs.
        return HardwareFingerprint(
            gpu_device=device.parent.name,
            gpu_total_bytes=gpu_total_bytes,
            cpu_arch=cpu_arch,
        )
    return HardwareFingerprint(gpu_device=None, gpu_total_bytes=None, cpu_arch=cpu_arch)


__all__ = ["discover_hardware"]
