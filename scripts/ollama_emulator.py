#!/usr/bin/env python3
"""Emulatore Ollama deterministico per l'ambiente sintetico (B-09).

Serve il sottoinsieme dell'API Ollama che il backend NewRay consuma
(``GET /api/version``, ``GET /api/tags``, ``POST /api/chat`` in
streaming NDJSON) con risposte deterministiche: stessa richiesta →
stesso stream, byte per byte. Nessun modello, nessuna GPU, nessun
egress: solo standard library.

Uso::

    python scripts/ollama_emulator.py --port 11534 --model newray-synthetic:latest

Contratto emulato (specifica vendor Ollama /api/chat):
- chunk progressivi ``{"model", "created_at", "message": {"role": "assistant", "content": ...}, "done": false}``
- riga finale ``{"model", "created_at", "message": {...}, "done": true, "done_reason": "stop", "total_duration", "prompt_eval_count", "eval_count"}``
- modello non installato → 404 ``{"error": "model ... not found"}``
- ``think`` e ``options`` vengono accettati e ignorati (il contratto
  del backend non dipende da valori di runtime reali).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIGEST = "sha256:0000000000000000000000000000000000000000000000000000000000000000"

#: Frasi di completamento: l'indice è derivato dal contenuto della
#: richiesta, in modo che la risposta sia deterministica ma dipenda
#: dall'input (utile per distinguere conversazioni nello script di catena).
PHRASES = (
    "Ricevuto: {n} messaggi nella conversazione.",
    "Confermo: l'ultimo messaggio è stato registrato in sequenza.",
    "Risposta sintetica deterministica per la verifica della catena.",
)


def deterministic_reply(messages: list[dict[str, object]]) -> str:
    """Contenuto del completamento: funzione pura dei messaggi in ingresso."""
    payload = json.dumps(messages, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    phrase = PHRASES[int(digest, 16) % len(PHRASES)]
    return phrase.format(n=len(messages))


class Handler(BaseHTTPRequestHandler):
    model: str = "newray-synthetic:latest"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # silenzioso: i log non devono dipendere dal traffico

    def _send_json(self, status: int, body: object) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/version":
            self._send_json(200, {"version": "0.0.0-synthetic"})
        elif self.path == "/api/tags":
            self._send_json(
                200,
                {
                    "models": [
                        {
                            "name": self.model,
                            "model": self.model,
                            "digest": DIGEST,
                            "size": 1,
                            "details": {"family": "synthetic"},
                        }
                    ]
                },
            )
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/chat":
            self._send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            self._send_json(400, {"error": "body non valido"})
            return
        model = body.get("model")
        if not isinstance(model, str) or model != self.model:
            self._send_json(404, {"error": f"model '{model}' not found"})
            return
        messages = body.get("messages")
        if not isinstance(messages, list) or not all(
            isinstance(m, dict) for m in messages
        ):
            self._send_json(400, {"error": "messaggi non validi"})
            return

        reply = deterministic_reply(messages)
        # Tre chunk progressivi deterministici + riga terminale.
        chunks = [reply[: max(1, len(reply) // 3)], reply[len(reply) // 3 : 2 * len(reply) // 3], reply[2 * len(reply) // 3 :]]
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for i, part in enumerate(chunks):
            chunk = {
                "model": self.model,
                "created_at": "2026-01-01T00:00:00Z",
                "message": {"role": "assistant", "content": part},
                "done": False,
            }
            self.wfile.write((json.dumps(chunk, ensure_ascii=False) + "\n").encode("utf-8"))
        final = {
            "model": self.model,
            "created_at": "2026-01-01T00:00:00Z",
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": "stop",
            "total_duration": 1,
            "prompt_eval_count": 10,
            "eval_count": len(reply),
        }
        self.wfile.write((json.dumps(final, ensure_ascii=False) + "\n").encode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=11534)
    parser.add_argument("--model", default="newray-synthetic:latest")
    args = parser.parse_args()
    Handler.model = args.model
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"emulatore Ollama deterministico su http://127.0.0.1:{args.port} (modello {args.model})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
