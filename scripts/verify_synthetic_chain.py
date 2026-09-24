#!/usr/bin/env python3
"""Verifica della catena completa sull'ambiente sintetico (B-09).

Esegue, contro l'API NewRay e PostgreSQL sintetici, la catena
documentata nel ticket: accesso, conversazione, profilo/modello,
run (fino al confine), eventi (fino al confine), stop e riavvio.

L'ambiente è quello preparato da ``scripts/ollama_emulator.py`` +
l'API avviata sul DB ``newray_it_synthetic``: nessuna dipendenza da
modelli reali, GPU o egress. Solo standard library.

Uso::

    python scripts/verify_synthetic_chain.py --api <origine WebUI>
        --model newray-synthetic:latest

``--api`` deve essere l'origine pubblica canonica — la WebUI che
proxyifica ``/api/v1/*`` sull'API (``http://127.0.0.1:5173`` di default
nel flusso ``.start --web``) — e non la porta grezza dell'API: l'
``SameOriginMiddleware`` richiede l'Origin canonico, quindi un client che
punti direttamente alla porta API grezza riceve ``403 ACCESS_DENIED``
("host non ammesso").

Contratto atteso per la parte NON implementata (B-04/B-06/B-07
"Da fare"): gli endpoint run/eventi non esistono e rispondono 404.
Lo script lo verifica come confine documentato, non come difetto:
fallisce solo se il confine cambia in modo inatteso (es. 500,
o endpoint parzialmente presenti).

Exit code 0 → catena verificata; 1 → difetto (il passo fallente è
stampato con metodo, percorso e corpo della risposta).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok " if condition else "FAIL"
    print(f"[{mark}] {label}" + (f" — {detail}" if detail and not condition else ""), flush=True)
    if not condition:
        FAILURES.append(f"{label}: {detail}")


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> tuple[int, dict[str, object] | list[object] | None]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers: dict[str, str] = {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        # Contratto same-origin (NewRay.md §§18.4, 20.3): le mutazioni
        # richiedono l'Origin canonica dell'API; il client si comporta
        # come la WebUI same-origin.
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            headers["Origin"] = self.base
        req = urllib.request.Request(
            self.base + path, data=data, method=method, headers=headers,
        )
        try:
            with self.opener.open(req, timeout=10) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, {"raw": raw.decode("utf-8", "replace")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8100")
    parser.add_argument("--model", default="newray-synthetic:latest")
    parser.add_argument("--name", default="catena-sintetica")
    parser.add_argument("--credential", default="cred-deterministica-0001")
    args = parser.parse_args()

    client = Client(args.api)
    print(f"== Catena sintetica su {args.api} (modello {args.model}) ==\n", flush=True)

    # --- 1. Accesso: stato, bootstrap monouso, me ---
    status, body = client.request("GET", "/api/v1/session/status")
    check("status iniziale", status == 200 and isinstance(body, dict), f"HTTP {status} {body}")
    bootstrapped = bool(body and isinstance(body, dict) and body.get("bootstrapped"))

    if not bootstrapped:
        status, body = client.request(
            "POST", "/api/v1/session",
            {"display_name": args.name, "credential": args.credential},
        )
        check("bootstrap owner", status == 201 and isinstance(body, dict) and "user_id" in (body or {}),
              f"HTTP {status} {body}")
        user_id = body["user_id"] if isinstance(body, dict) else None
    else:
        # Ambiente già inizializzato: si riprende con login (riavvio).
        status, body = client.request(
            "POST", "/api/v1/session/login",
            {"display_name": args.name, "credential": args.credential},
        )
        check("login owner esistente", status == 200 and isinstance(body, dict), f"HTTP {status} {body}")
        user_id = body["user_id"] if isinstance(body, dict) else None
    check("sessione attiva (cookie)", user_id is not None)

    status, body = client.request("GET", "/api/v1/me")
    check("GET /me", status == 200 and isinstance(body, dict) and body.get("user_id") == user_id,
          f"HTTP {status} {body}")

    # --- 2. Conversazione: crea, messaggio con idempotency, lista ---
    status, body = client.request("POST", "/api/v1/conversations", {"title": "Catena sintetica"})
    check("POST /conversations", status == 201 and isinstance(body, dict) and "id" in (body or {}),
          f"HTTP {status} {body}")
    conversation_id = body["id"] if isinstance(body, dict) else None

    status, body = client.request(
        "POST", f"/api/v1/conversations/{conversation_id}/messages",
        {"content": "primo messaggio", "idempotency_key": "key-0001"},
    )
    check("POST messaggio (idempotency)", status == 201 and isinstance(body, dict) and body.get("sequence") == 1,
          f"HTTP {status} {body}")
    first_message_id = body["id"] if isinstance(body, dict) else None

    status, body = client.request(
        "POST", f"/api/v1/conversations/{conversation_id}/messages",
        {"content": "primo messaggio", "idempotency_key": "key-0001"},
    )
    check("retry stessa chiave → stesso messaggio (stesso id, mai duplicato)",
          status == 201 and isinstance(body, dict) and body.get("id") == first_message_id
          and body.get("sequence") == 1,
          f"HTTP {status} {body}")

    status, body = client.request(
        "POST", f"/api/v1/conversations/{conversation_id}/messages",
        {"content": "secondo messaggio"},
    )
    check("secondo messaggio (sequenza server-side)",
          status == 201 and isinstance(body, dict) and body.get("sequence") == 2,
          f"HTTP {status} {body}")

    status, body = client.request("GET", f"/api/v1/conversations/{conversation_id}/messages")
    check("GET messaggi", status == 200 and isinstance(body, dict)
          and len(body.get("items", [])) == 2, f"HTTP {status} {body}")

    # --- 3. Profilo/modello: catalogo sintetico, binding risolto ---
    status, body = client.request("GET", "/api/v1/models")
    check("GET /models espone il modello sintetico",
          status == 200 and isinstance(body, dict)
          and any(m.get("name") == args.model for m in body.get("items", [])),
          f"HTTP {status} {body}")

    status, body = client.request("GET", "/api/v1/profiles")
    check("GET /profiles", status == 200 and isinstance(body, dict) and body.get("items"),
          f"HTTP {status} {body}")
    profile_id = body["items"][0]["id"] if isinstance(body, dict) and body.get("items") else None

    status, body = client.request("GET", f"/api/v1/profiles/{profile_id}/binding")
    check("GET binding risolto (digest presente)",
          status == 200 and isinstance(body, dict) and body.get("model_name") == args.model
          and isinstance(body.get("digest"), str) and body["digest"],
          f"HTTP {status} {body}")

    # --- 4. Run/eventi/stop: confine documentato (B-04/B-06/B-07 "Da fare") ---
    for path in ("/api/v1/runs", f"/api/v1/runs/{profile_id}/events", f"/api/v1/runs/{profile_id}/cancel"):
        status, _ = client.request("GET", path)
        check(f"confine: GET {path} → 404 (non implementato, non 5xx)", status == 404, f"HTTP {status}")

    # --- 5. Stop: revoca sessione, me non più raggiungibile ---
    status, _ = client.request("POST", "/api/v1/session/revoke")
    check("POST /session/revoke", status == 204, f"HTTP {status}")
    status, body = client.request("GET", "/api/v1/me")
    check("dopo revoca GET /me → 401", status == 401, f"HTTP {status} {body}")

    # --- 6. Riavvio: login con le stesse credenziali, stato preservato ---
    status, body = client.request(
        "POST", "/api/v1/session/login",
        {"display_name": args.name, "credential": args.credential},
    )
    check("riavvio: login dopo revoca", status == 200 and isinstance(body, dict)
          and body.get("user_id") == user_id, f"HTTP {status} {body}")
    status, body = client.request("GET", f"/api/v1/conversations/{conversation_id}/messages")
    check("riavvio: conversazione preservata (2 messaggi)",
          status == 200 and isinstance(body, dict) and len(body.get("items", [])) == 2,
          f"HTTP {status} {body}")

    # --- Negativo: credenziale errata → 401, mai un oracolo di scoperta ---
    status, body = client.request(
        "POST", "/api/v1/session/login",
        {"display_name": args.name, "credential": "cred-errata"},
    )
    check("login credenziale errata → 401", status == 401, f"HTTP {status} {body}")

    print()
    if FAILURES:
        print(f"CATENA FALLITA: {len(FAILURES)} difetti")
        for failure in FAILURES:
            print(f"  - {failure}")
        sys.exit(1)
    print("CATENA VERIFICATA: accesso, conversazione, profilo/modello, confine run, stop, riavvio.")


if __name__ == "__main__":
    main()
