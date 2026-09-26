#!/usr/bin/env python3
"""Qualifica il candidato pilot con rapporto atomico e record persistente (P-19).

Wrapper sottile su ``newray.modules.models.qualification_campaign.run_campaign``:
la logica delle sonde vive in ``qualify_local_models.py`` (funzioni pure);
qui le si compone in un :class:`RuntimeProbe` e si registra un
:class:`QualificationRecord` solo se **tutte** le sonde obbligatorie
passano, con hash di ``code``/``config``/``corpus`` per la riproducibilità.

Requisiti (runtime): Ollama in loopback, GPU libera, modello del catalogo
importato. Il default di qualifica è ristretto al solo candidato pilot
(``catalog.default_model``); ``--model`` seleziona un altro id **solo**
per decisione operativa esplicita.

Uso:

    backend/.venv/bin/python scripts/qualify_pilot.py \
        --output runtime/qualification-report.json

Su successo scrive anche il record in ``<data_dir>/qualifications/<model>.json``
(default: ``~/.local/share/newray/qualifications``).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from local_models import CATALOG, ROOT, load_catalog, local_host

sys.path.insert(0, str(ROOT / "backend/src"))

from newray.modules.models import (  # noqa: E402
    CampaignConfig,
    ProbeOutcome,
    RuntimeProbe,
    discover_hardware,
    run_campaign,
)
from newray.modules.models.adapters.qualification_file import (  # noqa: E402
    FileQualificationStore,
)
from newray.modules.models.qualification_report import (  # noqa: E402
    compute_code_hash,
    compute_config_hash,
    compute_corpus_hash,
)

CORPUS_DIR = ROOT / "packs" / "qualification"
DEFAULT_STORE_DIR = Path.home() / ".local" / "share" / "newray" / "qualifications"


class OllamaRuntimeProbe:
    """Adatta le sonde di ``qualify_local_models.py`` al contratto :class:`RuntimeProbe`.

    Ogni metodo delega a una funzione già esistente e la traduce in
    :class:`ProbeOutcome`. La cattura degli errori del runtime è
    responsabilità della campagna, che intercetta le eccezioni non
    gestite e le registra come ``failed``.
    """

    def __init__(self, model: dict, catalog: dict, host: str, timeout: int) -> None:
        # Import tardivo: ``qualify_local_models`` importa il backend, che
        # in ambienti di test non è sempre presente.
        from datetime import timedelta  # noqa: PLC0415

        from qualify_local_models import (  # noqa: PLC0415
            amd_vram_free,
            api,
            base_options,
        )

        from newray.infrastructure.network import HttpClient  # noqa: PLC0415
        from newray.modules.models.adapters.ollama import OllamaChatModel  # noqa: PLC0415

        self._model = model
        self._catalog = catalog
        self._host = host
        self._timeout = timeout
        self._api = api
        self._base_options = base_options
        self._amd_vram_free = amd_vram_free
        self._HttpClient = HttpClient
        self._OllamaChatModel = OllamaChatModel
        self._timedelta = timedelta
        # Risorse allocate in load(), rilasciate in unload().
        self._http_client = None
        self._chat_model = None
        self._digest: str | None = None
        self._declared: tuple[str, ...] = ()

    async def discover(self) -> ProbeOutcome:
        shown = self._api(self._host, "/api/show", {"model": self._model["name"]})
        tags = self._api(self._host, "/api/tags")
        entry = next((m for m in tags["models"] if m["name"] == self._model["name"]), None)
        if entry is None:
            return ProbeOutcome(
                status="failed",
                problems=(f"modello {self._model['name']!r} non nel catalogo runtime",),
            )
        self._digest = entry.get("digest")
        self._declared = tuple(shown.get("capabilities") or ())
        return ProbeOutcome(
            status="passed",
            digest=self._digest,
            declared_capabilities=self._declared,
            details={
                "size_bytes": entry.get("size"),
                "chat_template_chars": len(shown.get("template") or ""),
                "chat_template_is_passthrough": (
                    (shown.get("template") or "").strip() == "{{ .Prompt }}"
                ),
                "details": entry.get("details", {}),
            },
        )

    async def warmup(self) -> ProbeOutcome:
        size = self._model.get("size") or 0
        free = self._amd_vram_free()
        margin = 3 * 2**30
        if free is not None and size and free < size + margin:
            return ProbeOutcome(
                status="failed",
                problems=(
                    f"VRAM libera {free / 2**30:.1f} GiB, servono almeno "
                    f"{(size + margin) / 2**30:.1f} GiB. Liberare la GPU.",
                ),
                details={"vram_free_before": free},
            )
        return ProbeOutcome(status="passed", details={"vram_free_before": free})

    async def load(self) -> ProbeOutcome:
        name = self._model["name"]
        warmup = self._api(
            self._host,
            "/api/chat",
            {
                "model": name,
                "messages": [{"role": "user", "content": "ok"}],
                "stream": False,
                "options": {**self._base_options(self._catalog), "num_predict": 1},
            },
            timeout=self._timeout,
        )
        loaded = next(
            (m for m in self._api(self._host, "/api/ps")["models"] if m["name"] == name),
            {},
        )
        # Apre client per le sonde.
        self._http_client = self._HttpClient(
            base_url=self._host,
            connect_timeout=self._timedelta(seconds=5),
            read_timeout=self._timedelta(seconds=self._timeout),
        )
        self._chat_model = self._OllamaChatModel(self._http_client)
        return ProbeOutcome(
            status="passed",
            details={
                "size_vram": loaded.get("size_vram"),
                "context_length": loaded.get("context_length"),
                "load_seconds": round((warmup.get("load_duration") or 0) / 1e9, 3),
            },
        )

    async def probes(self) -> ProbeOutcome:
        """Esegue le sonde funzionali; ``qualified_capabilities`` deriva da
        quali sonde sono passate.

        - ``chat`` è dimostrata da ``probe_stream_basic`` +
          ``probe_system_instruction`` + ``probe_multi_turn`` +
          ``probe_truncation`` + ``probe_cancellation``;
        - ``tools`` è dimostrata da ``probe_tool_call``.
        """
        from qualify_local_models import (  # noqa: PLC0415
            probe_cancellation,
            probe_multi_turn,
            probe_stream_basic,
            probe_system_instruction,
            probe_tool_call,
            probe_truncation,
        )

        assert self._chat_model is not None
        name = self._model["name"]
        chat = self._chat_model
        chat_probes = {
            "stream_basic": await probe_stream_basic(chat, name, self._catalog, self._timeout),
            "system_instruction": await probe_system_instruction(
                chat, name, self._catalog, self._timeout
            ),
            "multi_turn": await probe_multi_turn(chat, name, self._catalog, self._timeout),
            "truncation": await probe_truncation(
                chat, name, self._catalog, self._host, self._timeout
            ),
            "cancellation": await probe_cancellation(
                chat, name, self._catalog, self._host, self._timeout
            ),
        }
        tool_probe = probe_tool_call(self._host, name, self._catalog, self._timeout)
        problems: list[str] = []
        for key, p in chat_probes.items():
            for problem in p["problems"]:
                problems.append(f"[{key}] {problem}")
        for problem in tool_probe["problems"]:
            problems.append(f"[tool_call] {problem}")

        qualified: list[str] = []
        if all(p["passed"] for p in chat_probes.values()):
            qualified.append("chat")
        if tool_probe["passed"]:
            qualified.append("tools")
        if problems:
            return ProbeOutcome(
                status="failed",
                problems=tuple(problems),
                qualified_capabilities=tuple(qualified),
                details={"probes": chat_probes, "tool_call": tool_probe},
            )
        return ProbeOutcome(
            status="passed",
            qualified_capabilities=tuple(qualified),
            details={"probes": chat_probes, "tool_call": tool_probe},
        )

    async def unload(self) -> ProbeOutcome:
        problems: list[str] = []
        try:
            self._api(
                self._host,
                "/api/generate",
                {"model": self._model["name"], "keep_alive": 0},
                timeout=120,
            )
        except (OSError, ValueError, KeyError) as exc:
            problems.append(f"{type(exc).__name__}: {exc}")
        try:
            still_loaded = any(
                m["name"] == self._model["name"] for m in self._api(self._host, "/api/ps")["models"]
            )
        except (OSError, ValueError, KeyError) as exc:
            problems.append(f"ps: {type(exc).__name__}: {exc}")
            still_loaded = True
        # Chiude il client HTTP anche in caso di errore.
        if self._http_client is not None:
            try:
                # ``aclose`` è async; ``asyncio.iscoroutinefunction`` non basta,
                # usiamo il metodo direttamente in ``await``.
                await self._http_client.aclose()
            except Exception as exc:  # noqa: BLE001
                problems.append(f"client_close: {type(exc).__name__}: {exc}")
            finally:
                self._http_client = None
                self._chat_model = None
        if still_loaded:
            problems.append("modello ancora presente in /api/ps dopo lo scaricamento")
        return ProbeOutcome(
            status="failed" if problems else "passed",
            problems=tuple(problems),
        )


def _build_config(
    model: dict,
    catalog_path: Path,
    corpus_dir: Path,
    thresholds_path: Path,
    output_path: Path,
    runtime: str,
) -> CampaignConfig:
    """Compone la ``CampaignConfig`` con gli hash del corpus e della config."""
    # code_hash: file di codice della qualifica
    code_paths = [
        Path(__file__).resolve(),
        (Path(__file__).parent / "qualify_local_models.py").resolve(),
        ROOT / "backend/src/newray/modules/models/qualification_campaign.py",
        ROOT / "backend/src/newray/modules/models/qualification.py",
        ROOT / "backend/src/newray/modules/models/qualification_report.py",
    ]
    code_hash = compute_code_hash(code_paths)

    # config_hash: catalogo + soglie + modello selezionato
    catalog_content = catalog_path.read_bytes()
    thresholds_content = thresholds_path.read_bytes() if thresholds_path.is_file() else b""
    config_hash = compute_config_hash(
        {
            "catalog_sha256": _sha256(catalog_content),
            "thresholds_sha256": _sha256(thresholds_content),
            "model_name": model["name"],
            "model_id": model["id"],
            "runtime": runtime,
        }
    )
    corpus_hash = compute_corpus_hash(corpus_dir if corpus_dir.is_dir() else None)

    hardware = discover_hardware()
    return CampaignConfig(
        model_name=model["name"],
        output_path=output_path,
        code_hash=code_hash,
        config_hash=config_hash,
        corpus_hash=corpus_hash,
        runtime=runtime,
        hardware=hardware,
    )


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


async def main_async(args: argparse.Namespace) -> int:
    host = local_host(args.host)
    catalog = load_catalog(args.catalog)
    # Default: solo candidato pilot. --model per override esplicito.
    default_name = catalog["default_model"]
    if args.model is None:
        model = next(m for m in catalog["models"] if m["name"] == default_name)
    else:
        selected = [m for m in catalog["models"] if m["id"] == args.model]
        if not selected:
            print(f"ID modello sconosciuto: {args.model}", file=sys.stderr)
            return 2
        model = selected[0]

    output = args.output.resolve()
    store = FileQualificationStore(args.store_dir.resolve())
    probe: RuntimeProbe = OllamaRuntimeProbe(model, catalog, host, args.timeout)
    config = _build_config(
        model,
        catalog_path=args.catalog,
        corpus_dir=args.corpus_dir,
        thresholds_path=args.corpus_dir / "thresholds.toml",
        output_path=output,
        runtime="ollama",
    )
    print(f"\n=== Qualifica {model['name']} ===", flush=True)
    print(f"  code_hash    = {config.code_hash[:16]}…", flush=True)
    print(f"  config_hash  = {config.config_hash[:16]}…", flush=True)
    print(f"  corpus_hash  = {config.corpus_hash[:16]}…", flush=True)
    print(f"  rapporto     = {output}", flush=True)

    result = await run_campaign(config=config, probe=probe, store=store)

    print(
        f"--- run_id={result.report.run_id} passed={result.passed} exit={result.exit_code}",
        flush=True,
    )
    for phase in result.report.phases.values():
        if phase.problems:
            for problem in phase.problems:
                print(f"    [{phase.name}] {problem}", flush=True)
    if result.record is not None:
        print(
            f"  record scritto: {model['name']} "
            f"(qualified={list(result.record.qualified_capabilities)})",
            flush=True,
        )
    else:
        print("  nessun record scritto (fasi fallite o capabilities vuote)", flush=True)
    return result.exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--model", help="ID del catalogo (override esplicito)")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "runtime" / "qualification-report.json",
        help="Percorso del rapporto JSON (assoluto)",
    )
    parser.add_argument(
        "--store-dir",
        type=Path,
        default=DEFAULT_STORE_DIR,
        help="Directory dei record di qualifica per host",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=CORPUS_DIR,
        help="Directory del corpus di qualifica",
    )
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
