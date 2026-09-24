# Deployment

Composizione dei servizi e configurazione operativa per Linux
(NewRay.md §21.1). A partire da A-07 (16 settembre 2026) esiste una
configurazione minimale eseguibile:

| Percorso | Contenuto |
| --- | --- |
| `compose.yaml` | Composizione minima: PostgreSQL/pgvector + API (host networking, bind loopback, volumi dedicati) |
| `Dockerfile` | Immagine API: Python 3.12 + uv bloccato, ambiente da `backend/uv.lock` (`--frozen --no-dev`) |
| `entrypoint.sh` | Migrazioni come ruolo proprietario, poi avvio API con settings validati |
| `db-init/00-roles.sh` | Prima inizializzazione: ruolo `newray_app` (senza superuser/BYPASSRLS) e estensione pgvector |
| `.env.example` | Modello delle credenziali locali: nessuna password predefinita |

Avvio, variabili d'ambiente, backup e limiti sono documentati in
[runtime e operazioni](../docs/operations/runtime.md).

Regole (NewRay.md §21): immagini con versioni bloccate; credenziali
applicative distinte da quelle delle migrazioni (ADR 0002); nessun socket
Docker nel backend; bind loopback iniziale — l'uso ufficio richiede
autenticazione, TLS e isolamento verificati. Ollama può girare sullo
stesso host o su endpoint interno gestito; parser, MCP, browser e search
sono opzionali e si aggiungono con i workflow che li richiedono.
