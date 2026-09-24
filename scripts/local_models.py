#!/usr/bin/env python3
"""Inventario/import GGUF locale in Ollama; solo standard library, niente pull.

python3 scripts/local_models.py list
python3 scripts/local_models.py import [--model base | --all] [--host http://127.0.0.1:11434]

Senza selezione, import agisce solo sul candidato pilot. --all e' una scelta
operativa esplicita; list mostra sempre l'inventario completo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

import tomllib

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "packs/catalogs/local-models.toml"


def load_catalog(path: Path = CATALOG) -> dict:
    with path.open("rb") as handle:
        catalog = tomllib.load(handle)
    if catalog.get("schema_version") != 1:
        raise ValueError("Versione catalogo non supportata")
    names = [model["name"] for model in catalog["models"]]
    ids = [model["id"] for model in catalog["models"]]
    if len(set(names)) != len(names) or len(set(ids)) != len(ids):
        raise ValueError("ID o alias duplicato nel catalogo")
    if names.count(catalog.get("default_model")) != 1:
        raise ValueError("Il modello pilot deve essere presente una volta nel catalogo")
    return catalog


def selected_models(
    catalog: dict, command: str, model_id: str | None, all_models: bool
) -> list[dict]:
    if model_id and all_models:
        raise ValueError("--model e --all sono alternativi")
    models = catalog["models"]
    if model_id:
        selected = [model for model in models if model["id"] == model_id]
    elif command == "list" or all_models:
        selected = models
    else:
        selected = [
            model for model in models if model["name"] == catalog["default_model"]
        ]
    if not selected:
        raise ValueError("ID modello assente dal catalogo")
    return selected


def local_host(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "L'import locale richiede un endpoint HTTP loopback senza credenziali/path"
        )
    return value.rstrip("/")


def api(host: str, path: str, payload: dict | None = None, timeout: int = 30) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(
        host + path, data=data, headers={"Content-Type": "application/json"}
    )
    # Le richieste loopback non passano da proxy d'ambiente.
    with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
        return json.load(response)


def artifact_path(catalog: dict, relative_file: str) -> Path:
    root = Path(catalog["model_root"]).resolve(strict=True)
    source = (root / relative_file).resolve(strict=True)
    if not source.is_relative_to(root) or source.suffix.lower() != ".gguf":
        raise ValueError("Sorgente fuori dalla raccolta GGUF")
    with source.open("rb") as handle:
        if handle.read(4) != b"GGUF":
            raise ValueError(f"File non GGUF: {source.name}")
    if any(ch in str(source) for ch in ("\n", "\r", '"')):
        raise ValueError("Percorso non rappresentabile in Modelfile")
    return source


def source_path(catalog: dict, model: dict) -> Path:
    return artifact_path(catalog, model["file"])


def projector_path(catalog: dict, model: dict) -> Path | None:
    relative = model.get("vision_projector_file")
    return artifact_path(catalog, relative) if relative is not None else None


def verify_source_digest(model: dict, source: Path, field: str = "sha256") -> str:
    expected = model.get(field)
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(
            f"Digest SHA-256 {field} assente o non valido per {model['id']}"
        )
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise ValueError(f"Digest SHA-256 non corrisponde per {model['id']}")
    return actual


def modelfile(catalog: dict, source: Path, projector: Path | None = None) -> str:
    return (
        f'FROM "{source}"\n'
        + (f'FROM "{projector}"\n' if projector is not None else "")
        + f"REQUIRES {catalog['minimum_ollama_version']}\n"
        + f"PARAMETER num_ctx {int(catalog['context_length'])}\n"
        + "PARAMETER temperature 0.2\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list", "import"])
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--model", help="ID del catalogo; omesso importa solo il pilot")
    parser.add_argument(
        "--all", action="store_true", help="importa esplicitamente tutti i modelli"
    )
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "runtime/local-models-import.json"
    )
    args = parser.parse_args()
    catalog = load_catalog(args.catalog)
    try:
        models = selected_models(catalog, args.command, args.model, args.all)
    except ValueError as error:
        parser.error(str(error))
    # Validare tutti i file prima di iniziare a modificare il catalogo Ollama.
    sources = [
        (model, source_path(catalog, model), projector_path(catalog, model))
        for model in models
    ]
    if args.command == "list":
        for model, source, projector in sources:
            print(
                f"{model['id']}: {model['name']} — {source.stat().st_size / 2**30:.2f} GiB"
                + (
                    f" + mmproj {projector.stat().st_size / 2**30:.2f} GiB"
                    if projector
                    else ""
                )
            )
        return 0
    host = local_host(args.host)
    version = api(host, "/api/version")["version"]
    actual = tuple(int(p) for p in version.split("-")[0].split("."))
    minimum = tuple(int(p) for p in catalog["minimum_ollama_version"].split("."))
    if actual < minimum:
        raise ValueError(
            f"Ollama {version}: serve almeno {catalog['minimum_ollama_version']}"
        )
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "ollama_version": version,
        "models": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for model, source, projector in sources:
        print(f"Importazione {model['name']} dal GGUF locale…", flush=True)
        before = source.stat()
        projector_before = projector.stat() if projector else None
        source_digest = (
            verify_source_digest(model, source) if model.get("sha256") else None
        )
        projector_digest = (
            verify_source_digest(model, projector, "vision_projector_sha256")
            if projector
            else None
        )
        with tempfile.TemporaryDirectory(prefix="newray-ollama-import-") as tmp:
            path = Path(tmp) / "Modelfile"
            path.write_text(modelfile(catalog, source, projector))
            command = subprocess.run(
                ["ollama", "create", model["name"], "-f", str(path)],
                env={**os.environ, "OLLAMA_HOST": host, "NO_COLOR": "1"},
                capture_output=True,
                check=False,
                text=True,
                timeout=1800,
            )
        if command.returncode:
            raise RuntimeError(
                f"Import {model['id']} fallito: {command.stderr[-2000:]}"
            )
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError(
                "Sorgente modificata durante l'import; rivalidare prima di usare"
            )
        if projector and projector_before:
            projector_after = projector.stat()
            if (
                projector_before.st_size,
                projector_before.st_mtime_ns,
            ) != (projector_after.st_size, projector_after.st_mtime_ns):
                raise RuntimeError("Proiettore modificato durante l'import; rivalidare")
        entry = next(
            m for m in api(host, "/api/tags")["models"] if m["name"] == model["name"]
        )
        shown = api(host, "/api/show", {"model": model["name"]})
        report["models"].append(
            {
                "id": model["id"],
                "name": model["name"],
                "source": str(source),
                "source_size": after.st_size,
                "source_mtime_ns": after.st_mtime_ns,
                "source_sha256": source_digest,
                "vision_projector_sha256": projector_digest,
                "digest": entry["digest"],
                "details": entry.get("details", {}),
                "declared_capabilities": shown.get("capabilities", []),
                "status": "installed",
                "modelfile": shown.get("modelfile", ""),
            }
        )
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(f"  registrato: {entry['digest']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
