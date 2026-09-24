#!/usr/bin/env python3
"""Qualifica i modelli locali del catalogo contro il percorso reale dell'app (B-03.4).

A differenza di ``smoke_local_models.py``, che verifica soltanto caricamento e
generazione sul runtime, questa prova esegue le sonde attraverso l'adapter
``OllamaChatModel`` e ``HttpClient`` del backend: quello che passa qui è quello
che passerà nel run. Le capacità che l'adapter non implementa ancora (tool call,
NewRay.md §10.3, ticket B-03.2-16) sono provate al livello del runtime e
registrate come tali, senza dedurne che l'app le supporti.

Requisiti: Ollama in esecuzione in loopback, GPU libera, ambiente del backend.

    backend/.venv/bin/python scripts/qualify_local_models.py

Esce con codice non zero se una sonda obbligatoria fallisce.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from local_models import CATALOG, ROOT, api, load_catalog, local_host

sys.path.insert(0, str(ROOT / "backend/src"))

from newray.infrastructure.network import (
    HttpClient,
    NetworkTimeout,
    NetworkUnreachable,
)
from newray.kernel.errors import (
    InferenceFailed,
    InferenceTimeout,
    ModelUnavailable,
)
from newray.modules.models.adapters.ollama import OllamaChatModel
from newray.modules.models.domain import (
    RUNTIME_OLLAMA,
    ChatMessage,
    ChatRequest,
    ChatRole,
    Completion,
    ContentDelta,
)

#: Marcatori di dialogo che non devono comparire nel testo visibile: la loro
#: presenza indica che il template di chat non è stato applicato o che il
#: parser del runtime non consuma i token speciali.
LEAKED_MARKERS = (
    "<|im_start|>",
    "<|im_end|>",
    "<start_of_turn>",
    "<end_of_turn>",
    "<think>",
    "</think>",
    "<tool_call>",
    "<|endoftext|>",
)

#: Continuazione del dialogo inventata dal modello: segnale di stop token
#: mancante (il modello prosegue oltre il proprio turno).
FAKE_TURN = re.compile(
    r"^\s*(user|utente|assistant|assistente|human)\s*:", re.IGNORECASE | re.MULTILINE
)

SYSTEM_CONCISE = "Sei l'assistente NewRay. Rispondi in italiano, in modo conciso."

#: Margine sopra la dimensione del modello: cache KV, contesto e overhead del
#: runtime non stanno nel peso del file.
VRAM_MARGIN_BYTES = 3 * 2**30


def amd_vram_free() -> int | None:
    """VRAM libera della GPU amdgpu in byte, o ``None`` se non rilevabile.

    Precondizione operativa, non un dettaglio: se un altro processo occupa la
    scheda (llama.cpp, una sessione precedente), Ollama può ripiegare su una
    GPU diversa — inclusa quella che pilota il display. Meglio fermarsi con un
    messaggio chiaro che scoprirlo da un freeze.
    """
    for device in sorted(Path("/sys/class/drm").glob("card*/device")):
        total, used = device / "mem_info_vram_total", device / "mem_info_vram_used"
        if total.is_file() and used.is_file():
            return int(total.read_text()) - int(used.read_text())
    return None


def base_options(catalog: dict[str, Any]) -> dict[str, object]:
    """Opzioni deterministiche: la qualifica deve essere ripetibile."""
    return {
        "num_ctx": int(catalog["context_length"]),
        "temperature": 0,
        "seed": 42,
    }


def visible_problems(text: str) -> list[str]:
    problems = [f"marcatore non consumato: {m}" for m in LEAKED_MARKERS if m in text]
    if FAKE_TURN.search(text):
        problems.append("il modello prosegue il dialogo oltre il proprio turno")
    return problems


async def collect(
    model: OllamaChatModel, request: ChatRequest, stop_after_deltas: int | None = None
) -> dict[str, Any]:
    """Consuma lo stream dell'adapter e misura ciò che vede l'app."""
    started = time.monotonic()
    result: dict[str, Any] = {
        "deltas": 0,
        "text": "",
        "first_delta_seconds": None,
        "completion": None,
        "error": None,
        "stopped_early": False,
    }
    chunks: list[str] = []
    stream = model.stream(request)
    try:
        async for event in stream:
            if isinstance(event, ContentDelta):
                if result["deltas"] == 0:
                    result["first_delta_seconds"] = round(time.monotonic() - started, 3)
                result["deltas"] += 1
                chunks.append(event.text)
                if (
                    stop_after_deltas is not None
                    and result["deltas"] >= stop_after_deltas
                ):
                    result["stopped_early"] = True
                    break
            elif isinstance(event, Completion):
                result["completion"] = {
                    "finish_reason": event.finish_reason,
                    "prompt_tokens": event.prompt_tokens,
                    "completion_tokens": event.completion_tokens,
                }
    except (InferenceFailed, InferenceTimeout, ModelUnavailable) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    except (NetworkTimeout, NetworkUnreachable) as exc:
        # Guasto mid-stream non mappato: l'adapter lascia salire un errore di
        # infrastruttura nel dominio (rilievo R10, ticket B-03.2-10). Va
        # registrato come difetto dell'app, non confuso con un difetto del modello.
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["unmapped_adapter_error"] = type(exc).__name__
    finally:
        # Cancellazione: chiudere il generatore rilascia la connessione e
        # Ollama interrompe la generazione (adapter, blocco finally).
        await stream.aclose()
    result["text"] = "".join(chunks)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def request_for(
    name: str,
    catalog: dict[str, Any],
    messages: list[ChatMessage],
    max_tokens: int,
    timeout_seconds: int,
) -> ChatRequest:
    return ChatRequest(
        model=name,
        runtime=RUNTIME_OLLAMA,
        messages=tuple(messages),
        parameters=base_options(catalog),
        max_tokens=max_tokens,
        inactivity_timeout=timedelta(seconds=timeout_seconds),
    )


async def probe_stream_basic(
    chat: OllamaChatModel, name: str, catalog: dict[str, Any], timeout: int
) -> dict[str, Any]:
    """Percorso nominale dell'app: stream, delta e completion contabilizzata."""
    run = await collect(
        chat,
        request_for(
            name,
            catalog,
            [
                ChatMessage(role=ChatRole.SYSTEM, content=SYSTEM_CONCISE),
                ChatMessage(
                    role=ChatRole.USER,
                    content="Elenca in una riga tre capoluoghi di regione italiani.",
                ),
            ],
            max_tokens=400,
            timeout_seconds=timeout,
        ),
    )
    problems = visible_problems(run["text"])
    if run["error"]:
        problems.append(run["error"])
    if not run["text"].strip():
        problems.append(
            "nessun contenuto visibile: l'app mostrerebbe una risposta vuota"
        )
    if run["completion"] is None:
        problems.append(
            "stream terminato senza completion: il run non saprebbe concludere"
        )
    if run["deltas"] < 2:
        problems.append(f"streaming non incrementale ({run['deltas']} delta)")
    return {"passed": not problems, "problems": problems, **run}


async def probe_system_instruction(
    chat: OllamaChatModel, name: str, catalog: dict[str, Any], timeout: int
) -> dict[str, Any]:
    """Il ruolo system deve raggiungere il modello con il template applicato."""
    run = await collect(
        chat,
        request_for(
            name,
            catalog,
            [
                ChatMessage(
                    role=ChatRole.SYSTEM,
                    content=(
                        "Sei l'assistente NewRay. Inizia ogni risposta con il "
                        "prefisso esatto [NR] e rispondi in italiano."
                    ),
                ),
                ChatMessage(role=ChatRole.USER, content="Quanto fa 2 + 2?"),
            ],
            max_tokens=300,
            timeout_seconds=timeout,
        ),
    )
    problems = visible_problems(run["text"])
    if run["error"]:
        problems.append(run["error"])
    if not run["text"].lstrip().startswith("[NR]"):
        problems.append("prefisso del system prompt non rispettato")
    return {"passed": not problems, "problems": problems, **run}


async def probe_multi_turn(
    chat: OllamaChatModel, name: str, catalog: dict[str, Any], timeout: int
) -> dict[str, Any]:
    """Cronologia a più turni: i ruoli devono restare distinti (§8.2)."""
    run = await collect(
        chat,
        request_for(
            name,
            catalog,
            [
                ChatMessage(role=ChatRole.SYSTEM, content=SYSTEM_CONCISE),
                ChatMessage(
                    role=ChatRole.USER,
                    content="Mi chiamo Stefano e il mio colore preferito è il verde.",
                ),
                ChatMessage(
                    role=ChatRole.ASSISTANT,
                    content="Ho annotato il tuo nome e il colore.",
                ),
                ChatMessage(
                    role=ChatRole.USER,
                    content="Qual è il mio colore preferito? Rispondi con una sola parola.",
                ),
            ],
            max_tokens=300,
            timeout_seconds=timeout,
        ),
    )
    problems = visible_problems(run["text"])
    if run["error"]:
        problems.append(run["error"])
    if "verde" not in run["text"].lower():
        problems.append(
            "cronologia non recuperata: il colore non compare nella risposta"
        )
    return {"passed": not problems, "problems": problems, **run}


async def probe_truncation(
    chat: OllamaChatModel, name: str, catalog: dict[str, Any], host: str, timeout: int
) -> dict[str, Any]:
    """Limite di token raggiunto: il motivo di fine deve essere distinguibile.

    Il run deve poter dire all'utente «risposta troncata». Se l'adapter riporta
    ``stop`` mentre il runtime dichiara ``length``, il troncamento diventa
    indistinguibile da un completamento e la UI mostra una risposta monca
    come se fosse finita (NewRay.md §8.3).
    """
    budget = 24
    messages = [
        ChatMessage(role=ChatRole.SYSTEM, content=SYSTEM_CONCISE),
        ChatMessage(
            role=ChatRole.USER,
            content="Elenca i numeri da 1 a 300 separati da virgola, senza commenti.",
        ),
    ]
    run = await collect(
        chat,
        request_for(
            name, catalog, messages, max_tokens=budget, timeout_seconds=timeout
        ),
    )
    problems = []
    if run["error"]:
        problems.append(run["error"])
    completion = run["completion"]
    if completion is None:
        problems.append("nessuna completion sul troncamento")

    # Verità del runtime sullo stesso limite, per confronto con l'adapter.
    raw_done_reason = None
    try:
        raw = api(
            host,
            "/api/chat",
            {
                "model": name,
                "messages": [
                    {"role": m.role.value, "content": m.content} for m in messages
                ],
                "stream": False,
                "options": {**base_options(catalog), "num_predict": budget},
            },
            timeout=timeout,
        )
        raw_done_reason = raw.get("done_reason")
    except (OSError, ValueError, KeyError) as exc:
        problems.append(
            f"confronto con il runtime non riuscito: {type(exc).__name__}: {exc}"
        )
    run["runtime_done_reason"] = raw_done_reason
    truncated_by_runtime = raw_done_reason == "length"
    if (
        completion is not None
        and truncated_by_runtime
        and completion["finish_reason"] != "length"
    ):
        problems.append(
            f"troncamento indistinguibile: il runtime dice {raw_done_reason!r}, "
            f"l'adapter riporta {completion['finish_reason']!r}"
        )
    if not run["text"].strip():
        problems.append(
            f"risposta vuota con num_predict={budget}: il budget è stato consumato "
            "senza contenuto visibile"
        )
    return {"passed": not problems, "problems": problems, **run}


async def probe_cancellation(
    chat: OllamaChatModel, name: str, catalog: dict[str, Any], host: str, timeout: int
) -> dict[str, Any]:
    """Stop esplicito: chiudere lo stream deve lasciare il runtime utilizzabile."""
    run = await collect(
        chat,
        request_for(
            name,
            catalog,
            [
                ChatMessage(role=ChatRole.SYSTEM, content=SYSTEM_CONCISE),
                ChatMessage(
                    role=ChatRole.USER,
                    content="Racconta in modo molto dettagliato la storia della stampa.",
                ),
            ],
            max_tokens=2000,
            timeout_seconds=timeout,
        ),
        stop_after_deltas=5,
    )
    problems = []
    if run["error"]:
        problems.append(run["error"])
    if not run["stopped_early"]:
        problems.append(
            "lo stream è terminato prima del punto di interruzione previsto"
        )
    # Il runtime deve restare utilizzabile subito dopo l'interruzione.
    after = await collect(
        chat,
        request_for(
            name,
            catalog,
            [ChatMessage(role=ChatRole.USER, content="Rispondi soltanto: pronto")],
            max_tokens=50,
            timeout_seconds=timeout,
        ),
    )
    if after["error"] or not after["text"].strip():
        problems.append(f"runtime non utilizzabile dopo lo stop: {after['error']}")
    run["reuse_after_stop"] = {
        "text": after["text"][:200],
        "error": after["error"],
        "elapsed_seconds": after["elapsed_seconds"],
    }
    run["still_loaded"] = any(m["name"] == name for m in api(host, "/api/ps")["models"])
    return {"passed": not problems, "problems": problems, **run}


def probe_thinking(
    host: str, name: str, catalog: dict[str, Any], timeout: int
) -> dict[str, Any]:
    """Con ``think: false`` esplicito, il campo ignorato deve restare vuoto.

    Riproduce il payload corrente dell'adapter, non il default del runtime.
    """
    started = time.monotonic()
    result: dict[str, Any] = {"passed": False, "problems": []}
    try:
        response = api(
            host,
            "/api/chat",
            {
                "model": name,
                "messages": [
                    {"role": "system", "content": SYSTEM_CONCISE},
                    {
                        "role": "user",
                        "content": "Quanti minuti ci sono in due ore e un quarto?",
                    },
                ],
                "stream": False,
                "think": False,
                "options": {**base_options(catalog), "num_predict": 600},
            },
            timeout=timeout,
        )
    except (OSError, ValueError, KeyError) as exc:
        result["problems"].append(f"{type(exc).__name__}: {exc}")
        return result
    message = response.get("message", {})
    thinking = message.get("thinking") or ""
    content = message.get("content") or ""
    result.update(
        {
            "thinking_chars": len(thinking),
            "content_chars": len(content),
            "content": content[:400],
            "thinking_excerpt": thinking[:200],
            "done_reason": response.get("done_reason"),
            "eval_count": response.get("eval_count"),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    )
    if thinking.strip():
        result["problems"].append(
            f"ragionamento in message.thinking ({len(thinking)} caratteri) scartato dall'adapter"
        )
    if not content.strip():
        result["problems"].append(
            "nessun contenuto: l'app mostrerebbe una risposta vuota"
        )
    result["passed"] = not result["problems"]
    return result


def probe_tool_call(
    host: str, name: str, catalog: dict[str, Any], timeout: int
) -> dict[str, Any]:
    """Ciclo tool a due passaggi sul runtime (l'adapter non lo implementa).

    Verifica l'assunzione centrale di NewRay.md §1: il modello del profilo
    chiama gli strumenti senza un coordinatore. Prima chiamata: il modello deve
    scegliere il tool e gli argomenti. Seconda: deve usare il risultato.
    """
    started = time.monotonic()
    result: dict[str, Any] = {"passed": False, "problems": []}
    tools = [
        {
            "type": "function",
            "function": {
                "name": "knowledge_search",
                "description": "Cerca passaggi nei documenti interni autorizzati dell'utente.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Testo da cercare nei documenti.",
                        }
                    },
                    "required": ["query"],
                },
            },
        }
    ]
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Sei l'assistente NewRay. Per rispondere su documenti interni usa "
                "sempre lo strumento knowledge_search. Rispondi in italiano."
            ),
        },
        {
            "role": "user",
            "content": "Nei nostri manuali, qual è il limite di rimborso per i pasti in trasferta?",
        },
    ]
    try:
        first = api(
            host,
            "/api/chat",
            {
                "model": name,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "think": False,
                "options": {**base_options(catalog), "num_predict": 600},
            },
            timeout=timeout,
        )
    except (OSError, ValueError, KeyError) as exc:
        result["problems"].append(f"prima chiamata: {type(exc).__name__}: {exc}")
        return result
    message = first.get("message", {})
    calls = message.get("tool_calls") or []
    result["tool_calls"] = calls
    result["first_content"] = (message.get("content") or "")[:300]
    if not calls:
        result["problems"].append(
            "nessun tool_call: il modello risponde senza usare lo strumento"
        )
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return result
    function = calls[0].get("function", {})
    if function.get("name") != "knowledge_search":
        result["problems"].append(f"strumento inatteso: {function.get('name')!r}")
    arguments = function.get("arguments")
    if not isinstance(arguments, dict) or not str(arguments.get("query", "")).strip():
        result["problems"].append(f"argomenti non utilizzabili: {arguments!r}")

    # Secondo passaggio: il risultato del tool deve entrare nella risposta.
    messages.append(
        {k: v for k, v in message.items() if k in {"role", "content", "tool_calls"}}
    )
    messages.append(
        {
            "role": "tool",
            "tool_name": "knowledge_search",
            "content": (
                "Manuale spese 2026, sezione 4.2: il limite di rimborso per i pasti "
                "in trasferta in Italia è di 46 euro al giorno."
            ),
        }
    )
    try:
        second = api(
            host,
            "/api/chat",
            {
                "model": name,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "think": False,
                "options": {**base_options(catalog), "num_predict": 600},
            },
            timeout=timeout,
        )
    except (OSError, ValueError, KeyError) as exc:
        result["problems"].append(f"seconda chiamata: {type(exc).__name__}: {exc}")
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return result
    final = (second.get("message", {}).get("content") or "").strip()
    result["final_content"] = final[:400]
    result["final_tool_calls"] = second.get("message", {}).get("tool_calls") or []
    if "46" not in final:
        result["problems"].append(
            "il risultato del tool non compare nella risposta finale"
        )
    result["problems"].extend(visible_problems(final))
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["passed"] = not result["problems"]
    return result


async def qualify(
    model: dict[str, Any], catalog: dict[str, Any], host: str, timeout: int
) -> dict:
    name = model["name"]
    shown = api(host, "/api/show", {"model": name})
    template = shown.get("template") or ""
    entry = next((m for m in api(host, "/api/tags")["models"] if m["name"] == name), {})
    record: dict[str, Any] = {
        "id": model["id"],
        "name": name,
        "digest": entry.get("digest"),
        "details": entry.get("details", {}),
        "declared_capabilities": shown.get("capabilities", []),
        "chat_template_chars": len(template),
        "chat_template_is_passthrough": template.strip() == "{{ .Prompt }}",
        "probes": {},
    }
    size = entry.get("size") or 0
    free = amd_vram_free()
    if free is not None and size and free < size + VRAM_MARGIN_BYTES:
        record["precondition_failed"] = (
            f"VRAM libera {free / 2**30:.1f} GiB, servono almeno "
            f"{(size + VRAM_MARGIN_BYTES) / 2**30:.1f} GiB per {name}. "
            "Liberare la GPU prima di qualificare: con la scheda occupata il "
            "runtime può ripiegare su un'altra GPU."
        )
        record["passed"] = False
        record["failed_probes"] = []
        return record
    record["vram_free_before"] = free

    client = HttpClient(
        base_url=host,
        connect_timeout=timedelta(seconds=5),
        read_timeout=timedelta(seconds=timeout),
    )
    chat = OllamaChatModel(client)
    started = time.monotonic()
    try:
        # Caricamento separato dalle sonde: altrimenti il tempo al primo token
        # della prima sonda misura il disco, non il modello.
        warmup = api(
            host,
            "/api/chat",
            {
                "model": name,
                "messages": [{"role": "user", "content": "ok"}],
                "stream": False,
                "options": {**base_options(catalog), "num_predict": 1},
            },
            timeout=timeout,
        )
        loaded = next(
            (m for m in api(host, "/api/ps")["models"] if m["name"] == name), {}
        )
        record["loaded"] = {
            "size_vram": loaded.get("size_vram"),
            "context_length": loaded.get("context_length"),
            "load_seconds": round((warmup.get("load_duration") or 0) / 1e9, 3),
        }
        record["probes"]["stream_basic"] = await probe_stream_basic(
            chat, name, catalog, timeout
        )
        record["probes"]["system_instruction"] = await probe_system_instruction(
            chat, name, catalog, timeout
        )
        record["probes"]["multi_turn"] = await probe_multi_turn(
            chat, name, catalog, timeout
        )
        record["probes"]["truncation"] = await probe_truncation(
            chat, name, catalog, host, timeout
        )
        record["probes"]["cancellation"] = await probe_cancellation(
            chat, name, catalog, host, timeout
        )
        record["probes"]["thinking"] = probe_thinking(host, name, catalog, timeout)
        record["probes"]["tool_call"] = probe_tool_call(host, name, catalog, timeout)
    finally:
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        try:
            api(host, "/api/generate", {"model": name, "keep_alive": 0}, timeout=120)
            record["unloaded"] = all(
                m["name"] != name for m in api(host, "/api/ps")["models"]
            )
        except (OSError, ValueError, KeyError) as exc:
            record["unload_error"] = f"{type(exc).__name__}: {exc}"
            record["unloaded"] = False
    probes = record["probes"]
    record["passed"] = all(p["passed"] for p in probes.values())
    record["failed_probes"] = sorted(k for k, p in probes.items() if not p["passed"])
    return record


async def main_async(args: argparse.Namespace) -> int:
    host = local_host(args.host)
    catalog = load_catalog(args.catalog)
    models = [
        m for m in catalog["models"] if args.model is None or m["id"] == args.model
    ]
    if not models:
        print("ID modello sconosciuto", file=sys.stderr)
        return 2
    report: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "ollama_version": api(host, "/api/version")["version"],
        "context_length": catalog["context_length"],
        "adapter": "newray.modules.models.adapters.ollama.OllamaChatModel",
        "models": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for model in models:
        print(f"\n=== Qualifica {model['name']} ===", flush=True)
        record = await qualify(model, catalog, host, args.timeout)
        report["models"].append(record)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        if "precondition_failed" in record:
            print(
                f"PRECONDIZIONE NON SODDISFATTA: {record['precondition_failed']}",
                flush=True,
            )
            return 3
        state = (
            "OK"
            if record["passed"]
            else f"FALLITE: {', '.join(record['failed_probes'])}"
        )
        print(f"--- {model['id']}: {state} ({record['elapsed_seconds']}s)", flush=True)
        for key, probe in record["probes"].items():
            for problem in probe["problems"]:
                print(f"    [{key}] {problem}", flush=True)
        if not record.get("unloaded"):
            print(
                "Scaricamento non confermato: interrompo prima del modello successivo."
            )
            return 1
    return 0 if all(m["passed"] for m in report["models"]) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--model", help="ID del catalogo, altrimenti tutti")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "runtime/local-models-qualification.json"
    )
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
