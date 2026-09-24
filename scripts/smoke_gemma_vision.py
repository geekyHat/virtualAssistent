"""Sonda visiva nativa opt-in con immagini sintetiche, senza file privati.

Non qualifica la catena NewRay: verifica solo tag/runtime Ollama. Il risultato
positivo richiede sia la capacità ``vision`` dichiarata da /api/show sia due
risposte corrette a immagini con colori opposti. Nessun modello alternativo.
"""

from __future__ import annotations

import argparse
import base64
import json
import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError

from local_models import CATALOG, ROOT, api, load_catalog, local_host


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def solid_png(rgb: tuple[int, int, int]) -> bytes:
    """PNG 64x64 RGB generato in memoria, senza dipendenze o download."""
    width = height = 64
    row = b"\x00" + bytes(rgb) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(row * height))
        + _chunk(b"IEND", b"")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--model", help="Alias locale da provare; default dal catalogo")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "runtime/gemma-vision-smoke.json"
    )
    args = parser.parse_args()
    catalog = load_catalog(args.catalog)
    host = local_host(args.host)
    model = args.model or catalog["default_model"]
    shown = api(host, "/api/show", {"model": model})
    declared = shown.get("capabilities", [])
    installed = next(
        (
            item
            for item in api(host, "/api/tags")["models"]
            if item.get("name") == model
        ),
        None,
    )
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "ollama_version": api(host, "/api/version")["version"],
        "model": model,
        "digest": installed.get("digest") if installed else None,
        "declared_capabilities": declared,
        "cases": [],
    }
    try:
        for color, rgb in (("rosso", (255, 0, 0)), ("blu", (0, 0, 255))):
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": "Di quale colore uniforme è questa immagine? Rispondi con una sola parola.",
                        "images": [base64.b64encode(solid_png(rgb)).decode("ascii")],
                    }
                ],
                "stream": False,
                "think": False,
                "keep_alive": "2m",
                "options": {
                    "num_ctx": int(catalog["context_length"]),
                    "num_predict": 32,
                    "temperature": 0,
                },
            }
            case = {"expected": color, "passed": False}
            try:
                response = api(host, "/api/chat", payload, timeout=args.timeout)
                content = str(response.get("message", {}).get("content", "")).strip()
                case.update(
                    {
                        "content": content[:200],
                        "done": response.get("done"),
                        "passed": response.get("done") is True
                        and content.casefold().strip(" .!\n") == color,
                    }
                )
                if "loaded" not in report:
                    report["loaded"] = next(
                        (
                            item
                            for item in api(host, "/api/ps")["models"]
                            if item.get("name") == model
                        ),
                        None,
                    )
            except HTTPError as exc:
                case["http_status"] = exc.code
                case["error"] = exc.read(300).decode("utf-8", errors="replace")
            report["cases"].append(case)
            if "http_status" in case:
                break
    finally:
        try:
            api(host, "/api/generate", {"model": model, "keep_alive": 0}, timeout=60)
            report["unloaded"] = all(
                item.get("name") != model for item in api(host, "/api/ps")["models"]
            )
        except (OSError, ValueError, KeyError):
            report["unloaded"] = False
        report["vision_qualified_natively"] = (
            "vision" in declared
            and len(report["cases"]) == 2
            and all(case["passed"] for case in report["cases"])
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["vision_qualified_natively"] and report["unloaded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
