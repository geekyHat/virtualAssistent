# Automazione del repository

Gli script nascono con operazioni reali e verificabili, nessuno stub: ogni
script deve fallire con exit code non zero quando la verifica fallisce.

| Script | Stato | Responsabilità |
| --- | --- | --- |
| generate_contracts.py | Disponibile (A-05) | Esporta i DTO HTTP in `contracts/openapi.json` e genera i tipi TypeScript in `web/src/shared/contracts/`; `--check` per il drift |
| start_local.py | Avvio personale Linux | Invocato da `.start`: provisioning PostgreSQL privato, migrazioni, readiness API/proxy/WebUI, runtime Ollama configurato e cleanup dei processi posseduti |
| check_architecture.py | Disponibile (A-06, rafforzato B-03.2-12) | Confini degli import del backend (inclusi core→adapter/infrastructure e adapter→interfaces), framework, file non classificati, import dinamici e cicli; sola standard library Python |

Comandi verificati il 16 settembre 2026 (dal root):

- `python scripts/generate_contracts.py` — rigenerazione deterministica
  byte a byte; `--check`: drift rilevato con exit 1, assenza con exit 0.
- `python scripts/check_architecture.py` — 33 file verificati, exit 0;
  verificato anche il negativo (cross-modulo privato, framework in dominio,
  dipendenza kernel→infrastructure e ciclo a due file → exit 1).

Entrambi sono richiamati dalla CI (`.github/workflows/ci.yml`, A-06).
Packaging e controlli aggiuntivi verranno introdotti insieme al relativo
artefatto.

Avvio personale: `./.start --web` dal root, dettagli nella guida runtime.
Contratti del launcher: `backend/.venv/bin/python scripts/tests/test_start_local.py`
(7 offline; smoke di processo saltato per default). Smoke opt-in:
`NEWRAY_LIVE_START_TEST=1 backend/.venv/bin/python scripts/tests/test_start_local.py`.
Richiede PostgreSQL/pgvector nativi e socket loopback; usa un cluster effimero
con identità sintetica e verifica primo avvio, riuso, shutdown e riavvio con
sessione preservata. Non legge `.env` dell'utente né usa modelli/GPU.
