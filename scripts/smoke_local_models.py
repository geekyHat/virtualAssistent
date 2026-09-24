#!/usr/bin/env python3
"""Carica/genera/scarica ogni modello esplicito del catalogo, senza fallback.

Eseguire quando la GPU non è utilizzata da llmctl/altre sessioni.
Salva anche i fallimenti e restituisce exit code nonzero se un modello fallisce.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from local_models import CATALOG, ROOT, api, load_catalog, local_host


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--model", help="ID del catalogo, altrimenti tutti")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "runtime/local-models-smoke.json"
    )
    args = parser.parse_args()
    host = local_host(args.host)
    catalog = load_catalog(args.catalog)
    models = [
        m for m in catalog["models"] if args.model is None or m["id"] == args.model
    ]
    if not models:
        parser.error("ID modello sconosciuto")
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "ollama_version": api(host, "/api/version")["version"],
        "prompt": "Rispondi soltanto con la parola OK.",
        "context_length": catalog["context_length"],
        "models": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for model in models:
        name = model["name"]
        print(f"Test caricamento e generazione: {name}", flush=True)
        started = time.monotonic()
        result = {"id": model["id"], "name": name, "passed": False}
        try:
            response = api(
                host,
                "/api/chat",
                {
                    "model": name,
                    "messages": [{"role": "user", "content": report["prompt"]}],
                    "stream": False,
                    "think": False,
                    "keep_alive": "2m",
                    "options": {
                        "num_ctx": catalog["context_length"],
                        "num_predict": 64,
                        "temperature": 0,
                        "seed": 42,
                    },
                },
                timeout=args.timeout,
            )
            loaded = next(
                m for m in api(host, "/api/ps")["models"] if m["name"] == name
            )
            content = response.get("message", {}).get("content", "")
            result.update(
                {
                    "done": response.get("done"),
                    "done_reason": response.get("done_reason"),
                    "content": content,
                    "eval_count": response.get("eval_count"),
                    "load_duration_ns": response.get("load_duration"),
                    "eval_duration_ns": response.get("eval_duration"),
                    "loaded": loaded,
                    "passed": response.get("done") is True and bool(content.strip()),
                }
            )
        except (OSError, ValueError, KeyError, StopIteration) as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                api(host, "/api/generate", {"model": name, "keep_alive": 0}, timeout=60)
                result["unloaded"] = all(
                    m["name"] != name for m in api(host, "/api/ps")["models"]
                )
            except (OSError, ValueError, KeyError) as exc:
                result["unload_error"] = f"{type(exc).__name__}: {exc}"
                result["unloaded"] = False
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            report["models"].append(result)
            args.output.write_text(
                json.dumps(report, indent=2, ensure_ascii=False) + "\n"
            )
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not result["unloaded"]:
            print(
                "Scaricamento non confermato: interrompo prima del modello successivo."
            )
            return 1
    return 0 if all(m["passed"] for m in report["models"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
