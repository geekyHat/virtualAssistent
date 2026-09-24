# Backend

Destinazione del backend Python 3.12/FastAPI secondo NewRay.md §§4–8.
Questa area contiene il pacchetto `newray` (stack bloccato in A-01) e le
istruzioni di sviluppo.

| Percorso | Contenuto |
| --- | --- |
| src/newray/bootstrap/ | App API (entrypoint uvicorn) e worker dei run durevoli (`python -m newray.bootstrap.worker`, P-05), composizione e settings validati |
| src/newray/kernel/ | Principal/ID, errori e clock iniettabile — creato in A-02 |
| src/newray/modules/ | identity, profiles, conversations, models e `runs` (preview inline transitoria, run durevoli e worker P-05); dominio/casi d'uso dipendono solo da porte pubbliche, gli adapter PostgreSQL/Ollama restano ai bordi |
| src/newray/interfaces/http/ | middleware identity (A-02); route, DTO Pydantic e error mapping (NewRay.md §19) — creati in A-04 |
| src/newray/infrastructure/ | database (engine, contesto transazionale) — A-03; network (client egress httpx, unico punto del vendor) — B-03; processi, segreti e osservabilità con i relativi casi d'uso (incrementi B/C) |
| migrations/ | Revisioni Alembic — da 0001 identity a 0010 worker/scheduler dei run durevoli (P-05); tabelle, backfill, RLS e grant evolvono solo tramite migrazioni |
| scripts/db/ | bootstrap.sql: ruoli DB, database ed estensione pgvector (superuser, solo sviluppo locale) |
| tests/ | unit, contracts (fake) e integration (PostgreSQL reale, RLS) — integration creato in A-03 |

L'[albero completo](../docs/architecture/structure.md) elenca i moduli previsti.
Le porte vengono definite solo quando il caso d'uso ne ha bisogno.

## PostgreSQL (A-03)

Dal consolidamento B-03.2-11, il lifespan API crea e dispone un solo engine
condiviso fra identity, conversations e profiles. I builder ricevono
esplicitamente quell'engine; gli adapter non possiedono il pool. Lo stack di
cleanup chiude anche il client Ollama se il setup fallisce e dispone il pool
anche quando la chiusura del client genera un errore.

Le operazioni asincrone dei profili spostano seeding e letture DB in un unico
blocco sincrono eseguito in un thread, poi consultano il catalogo una volta
per operazione. Nessuna transazione rimane aperta durante la rete. Pool e
timeout sono configurabili: vedere la tabella della guida runtime. Cancellare
il task non ferma il thread; i timeout PostgreSQL ne limitano le attese.

Ruoli separati (NewRay.md §7.3, ADR 0002): `newray_migrate` possiede le
migrazioni e le tabelle; `newray_app` è il ruolo applicativo, senza
superuser né BYPASSRLS. La RLS sulle tabelle sensibili è FORCED: vale anche
per il proprietario. Il contesto del principal è impostato per transazione
(`set_config` locale, pulito a commit/rollback).

Setup di una istanza locale (da superuser, una volta sola):

```sh
psql -v ON_ERROR_STOP=1 -f backend/scripts/db/bootstrap.sql
export NEWRAY_MIGRATION_DATABASE_URL="postgresql+psycopg://newray_migrate@127.0.0.1:5432/newray"
uv run alembic upgrade head
```

Le password di `bootstrap.sql` valgono solo per sviluppo locale, mai per la
produzione. I test di integrazione richiedono tre DSN:
`NEWRAY_TEST_ADMIN_URL` (superuser), `NEWRAY_TEST_MIGRATION_URL`
(newray_migrate) e `NEWRAY_TEST_DATABASE_URL` (newray_app); senza di essi si
saltano e la suite ordinaria resta offline.

## Applicazione (A-04)

L'app API è composta in `newray.bootstrap.wiring` (unico punto di
composizione) e servita da uvicorn. Il principal è sempre risolto dal
server a partire dal cookie opaco `newray_session` (HttpOnly,
SameSite=Lax, Secure solo se configurato): gli ID del client non sono mai
letti. Il bootstrap del proprietario è monouso: la seconda chiamata
risponde 409 CONFLICT.

```sh
export NEWRAY_DATABASE_DSN="postgresql+psycopg://newray_app@127.0.0.1:5433/newray"
export NEWRAY_MIGRATION_DATABASE_URL="postgresql+psycopg://newray_migrate@127.0.0.1:5433/newray"
uv run alembic upgrade head
uv run python -m newray.bootstrap.main   # bind e porta dai settings (127.0.0.1:8000)
```

L'avvio unico è `python -m newray.bootstrap.main` (A-07): un bind
non-loopback senza `NEWRAY_COOKIE_SECURE=true` viene rifiutato dai
settings (NewRay.md §20.3). Dettaglio operativo in
[docs/operations/runtime.md](../docs/operations/runtime.md).

Superficie attuale (NewRay.md §19.2):

| Endpoint | Comportamento |
| --- | --- |
| `POST /api/v1/session` | Bootstrap monouso: crea org, proprietario e sessione; 201 + `Set-Cookie` |
| `GET /api/v1/me` | Identità del principal risolto dal server; 401 se assente/revocato |
| `POST /api/v1/session/revoke` | Revoca immediata; 204 + cancellazione del cookie |
| `GET /api/v1/conversations` | Conversazioni proprie, dalle più recenti; pagina a cursore (`cursor`, `limit` ≤ 200) |
| `POST /api/v1/conversations` | Nuova conversazione; 201 (titolo 1–200) |
| `GET /api/v1/conversations/{id}` | Conversazione; 404 fuori scope senza rivelarne l'esistenza |
| `PATCH /api/v1/conversations/{id}` | Rinomina: aggiorna `updated_at` (ordine della lista) |
| `DELETE /api/v1/conversations/{id}` | Cancellazione fisica con i messaggi (cascata); 204 |
| `GET /api/v1/conversations/{id}/messages` | Messaggi in ordine di sequenza da `after_sequence`; `next_sequence` |
| `POST /api/v1/conversations/{id}/messages` | Messaggio dell'utente (contenuto 1–50 000); ruolo e sequenza decisi dal server; 201 |
| `POST /api/v1/conversations/{id}/run` | Preview SSE inline: **non più chiamata dalla WebUI** dalla chiusura P-06 (route/contratto preservati per compatibilità, mai per funzionalità nuova); binding validato, budget esplicito, stream del modello del profilo e coppia user/assistant atomica; `Idempotency-Key` opzionale |
| `POST /api/v1/conversations/{id}/runs` | Crea un run durevole idempotente (`idempotency_key` obbligatoria); il worker lo consuma in background (P-05); 201 con lo snapshot pubblico |
| `GET /api/v1/conversations/{id}/active-run` | Run non terminale più recente della conversazione, o `null` (P-06): permette a una scheda nuova o a un refresh di ritrovare e riprendere un run in corso senza già possederne l'id |
| `GET /api/v1/runs/{id}` | Snapshot pubblico del run; 404 fuori scope |
| `POST /api/v1/runs/{id}/cancel` | Cancellazione persistita e idempotente (P-06): un run `queued` transita subito, un run `running` la vede propagata dal worker al prossimo heartbeat |
| `GET /api/v1/runs/{id}/events` | Eventi durevoli in SSE con replay/cursore (`after_sequence`) e `resync` su cursore scaduto (P-06); poll interno, nessun LISTEN/NOTIFY |
| `GET /api/v1/profiles` | Lettura senza effetti dei profili propri; conserva anche gli specialisti storici, `model: null` se il binding non è disponibile |
| `POST /api/v1/profiles/defaults` | Provisioning esplicito e idempotente del solo Assistente; 200 con profilo corrente, nessun download dei pesi |
| `GET /api/v1/models` | Modelli del runtime, dallo stato della macchina (catalogo onesto, §9.1: vuoto senza Ollama collegato; runtime irraggiungibile → vuoto; timeout → 504 `INFERENCE_TIMEOUT`) |
| `GET /api/v1/models/readiness` | Diagnostica autenticata e senza load: distingue runtime non configurato/irraggiungibile, catalogo vuoto, modello mancante, errore, artefatto senza chat e installazione non qualificata; le capacità di `/api/show` sono solo dichiarate |
| `GET /api/v1/profiles/{id}/binding` | Snapshot di risoluzione riproducibile (versione, binding, digest, capacità); 404 fuori scope, 503 `MODEL_UNAVAILABLE` se il modello manca. Gli specialisti storici restano leggibili, ma la preview run del pilot accetta solo Assistente |

Il default di configurazione per i nuovi Assistenti è
`newray-gemma4-31b-it:ud-q4-k-xl-vision-v1` (ADR 0006), composto dagli stessi
pesi Gemma e dal proiettore locale. Profili, versioni e binding storici non
cambiano per un nuovo default;
una transizione del modello richiede una nuova versione esplicita.
Ogni richiesta NewRay imposta `num_ctx=8192` salvo override esplicito del
binding, con massimo configurabile iniziale 16384
(`NEWRAY_MODEL_CONTEXT_LENGTH`/`NEWRAY_MODEL_MAX_CONTEXT_LENGTH`). Un valore
fuori budget viene respinto prima della chiamata a Ollama. Il tag esistente e
il suo digest non sono riscritti da questa impostazione.

L'egress verso Ollama è opzionale: con `NEWRAY_OLLAMA_BASE_URL` (http/https,
validata dai settings) il catalogo legge `GET /api/tags`; senza, nessun
runtime è collegato e la risoluzione risponde `MODEL_UNAVAILABLE`
recuperabile (ADR 0003), mai un fallback Echo. Lo streaming vendor
(`POST /api/chat`, NDJSON) è implementato dall'adapter `OllamaChatModel`
(porta `ChatModel`): i limiti della richiesta diventano `options` del
runtime, la cancellazione è la chiusura dello stream e i guasti vendor
sono mappati sui codici stabili `MODEL_UNAVAILABLE` (404/irraggiungibile),
`INFERENCE_FAILED` (502) e `INFERENCE_TIMEOUT` (504, retryable) — nessun
dettaglio vendor nei payload pubblici. Il terminale della preview espone
`completion_tokens`, `eval_duration_ns` e `tokens_per_second` quando Ollama
fornisce conteggio e durata validi; dati mancanti restano `null`.

Gli errori restituiscono codici stabili con payload fisso
`{code, message, retryable, correlation_id}` (NewRay.md §19.4). I DSN non
compaiono mai nei log. Limiti dichiarati: l'hardening CSRF completo
(arrivi da origini esterne) con l'edizione ufficio — qui SameSite=Lax e
origine singola bastano per lo sviluppo loopback. Bind, porta, cookie
Secure, `NEWRAY_DATA_DIR` (da A-07) e `NEWRAY_OLLAMA_BASE_URL` (da B-03)
sono validati dai settings.

## Stack bloccato (A-01, 16 settembre 2026)

`pyproject.toml` + `uv.lock` (45 pacchetti, CPython 3.12.12).

| Dipendenza | Versione bloccata | Licenza |
| --- | --- | --- |
| fastapi | 0.141.1 | MIT |
| uvicorn[standard] | 0.53.0 | BSD-3-Clause |
| pydantic | 2.13.5 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| sqlalchemy | 2.0.54 | MIT |
| alembic | 1.20.0 | MIT |
| psycopg[binary,pool] | 3.3.5 | LGPL-2.1-or-later |
| httpx | 0.28.1 | BSD-3-Clause |
| ruff (dev) | 0.16.7 | MIT/Apache-2.0 |
| mypy (dev) | 2.3.1 | MIT |
| pytest (dev) | 9.1.1 | MIT |

psycopg è LGPL-2.1 (scelta di base NewRay.md §4.2): compatibile con l'uso
previsto; la licenza del progetto resta da decidere prima della pubblicazione.

## Comandi verificati (24 settembre 2026, slice ciclo tool P-07)

Ambiente: PostgreSQL 16 + pgvector 0.6.0 nativi (cluster `127.0.0.1:5432`,
ruoli `newray_migrate`/`newray_app`/`newray_scheduler`). Nessun Ollama/GPU:
ciclo tool provato con `ChatModel` fake (l'adapter Ollama non emette ancora
`ToolCallRequest`; live tool use = P-20).

- `.venv/bin/python -m pytest -q tests/unit tests/contracts tests/integration`
  da `backend/`: **433 passed**, 2 warning di terze parti. Nuovi:
  `test_tool_gateway.py` (6), `test_tool_invocations_integration.py` (3,
  PostgreSQL reale: ricevuta idempotente + eventi tool in ordine, fencing,
  RLS), estensioni a `test_run_worker.py` (3 sul ciclo tool) e
  `test_http_run_events.py` (4 su `GET /runs/{id}/tools` e eventi `tool.*`).
- `.venv/bin/ruff check src tests`, `.venv/bin/ruff format --check src tests`,
  `.venv/bin/mypy src` (82 sorgenti): verdi.
- `python scripts/check_architecture.py` (dal root): 82 file, verde
  (`tools` importa `runs.public`, `runs` non importa `tools`: nessun ciclo).
- `python scripts/generate_contracts.py --check` (dal root): nessun drift
  dopo la rigenerazione con `GET /runs/{id}/tools`.
- `web`: `npm run check` (format/lint/typecheck/**88 Vitest**/licenze/build/
  bundle 466,2 KB JS): verde. `npx playwright test --project=ui`: **57
  passed, 7 skipped** (stesso limite Playwright di P-05/P-06: eseguibile
  puntato via modifica locale non commessa, ripristinata prima del commit).
- `NEWRAY_LIVE_START_TEST=1 scripts/tests/test_start_local.py`: **8/8**,
  incluse le migrazioni fino alla 0012 e il worker con il gateway tool.

## Comandi verificati (24 settembre 2026, chiusura P-06)

Ambiente: PostgreSQL 16 + pgvector 0.6.0 nativi (cluster `127.0.0.1:5432`,
ruoli `newray_migrate`/`newray_app`/`newray_scheduler` da
`backend/scripts/db/bootstrap.sql`). Nessun Ollama/GPU: worker provato con
`EchoChatModel`/fake, la stessa scelta di P-05.

- `.venv/bin/python -m pytest -q tests/unit tests/contracts tests/integration`
  da `backend/`: **418 passed**, 2 warning di terze parti. Include
  `test_run_events_integration.py` (4, PostgreSQL reale: sequenza monotona
  isolata per scope, retention dei delta con `resync` su cursore scaduto,
  cancel `queued`→transizione diretta, cancel `running`→propagato dal
  worker reale con `ChatModel` fake lento e gate temporale sull'heartbeat),
  `test_http_run_events.py` (11, contratto: replay completo/parziale,
  resync, 404 fuori scope, cancel idempotente, più i 4 nuovi su
  `GET /conversations/{id}/active-run`: nessun run, run in coda/in
  esecuzione, run terminale non più ripresentato, conversazione fuori
  scope → `null` non 404), `test_rls_runs.py` esteso con
  `test_run_attivo_per_conversazione_scoped_e_solo_non_terminale`
  (PostgreSQL reale: nessun run→`None`, trovato in coda e in esecuzione,
  fuori scope→`None` senza errore distinto sotto RLS, terminale→`None`).
- `.venv/bin/ruff check src tests`, `.venv/bin/ruff format --check src
  tests`, `.venv/bin/mypy src` (74 sorgenti): verdi.
- `python scripts/check_architecture.py` (dal root): 74 file verificati, verde.
- `python scripts/generate_contracts.py --check` (dal root): nessun drift
  dopo la rigenerazione che ha aggiunto `active-run`/eventi/cancel a
  `contracts/openapi.json` e `web/src/shared/contracts/api.d.ts`.
- `web`: `npm run check` (format/lint/typecheck/**85 Vitest**/licenze 640
  pacchetti/build/bundle 465,7 KB JS – 26,2 KB CSS): verde. Vitest nuovi:
  `runEventsReducer.test.ts` (17: dedup per sequence, buco→`desync`,
  resync riallinea baseline e testo, terminale unico su replay/riconnessione,
  `run.failed`/`run.cancelled`/`run.interrupted` mappati per tipo evento non
  per solo `finish_reason` — due bug reali trovati e corretti scrivendo
  questi test, non solo confermati), `sse.test.ts` esteso per `id:` (14).
- `npx playwright test --project=ui` (`web/e2e`): **55 passed, 7 skipped**
  (62 totali) con l'eseguibile Chromium disponibile nell'ambiente
  (`/opt/pw-browsers/chromium`, revisione 1194) puntato via
  `launchOptions.executablePath` **non commesso** — modifica locale a
  `playwright.config.ts`, verificata e poi ripristinata all'originale
  prima del commit (`git diff` pulito), perché la revisione richiesta dal
  pacchetto (1243) non è installata in questo ambiente di sviluppo,
  stesso limite già osservato in P-05; il comando di default `npm run
  e2e` resta quindi non eseguibile qui senza quella stessa modifica
  locale. `chat-sse.spec.ts` (14 scenari della route inline) è
  `test.describe.skip`, documentato nel file: quella route non è più
  raggiunta dalla WebUI, guidare "Invia" non intercetta più nulla — gli
  stessi scenari (terminali distinti, `error` dopo delta, EOF senza
  terminale, stop) sono riportati su `run-events.spec.ts` (14 test, inclusi
  2 di resume) sul percorso realmente usato; `workspace.spec.ts` (race
  cambio-conversazione/logout-durante-run) migrato alle nuove route.
  4 esecuzioni consecutive di `run-events.spec.ts` da solo: 14/14 verdi
  ogni volta; una singola race trovata e corretta durante questo lavoro
  era nel TEST (la conversazione appena creata aveva un proprio
  resume-check in corso quando il run veniva seminato subito dopo — non un
  difetto dell'hook, risolto navigando via prima di seminare il run).
  Un fallimento isolato di `navigation.spec.ts` (non toccato da questa
  chiusura) durante UNA delle esecuzioni della suite completa non si è
  ripetuto in isolamento né nelle esecuzioni successive: flakiness
  preesistente dell'ambiente, non una regressione di questa chiusura.
- `NEWRAY_LIVE_START_TEST=1 backend/.venv/bin/python
  scripts/tests/test_start_local.py` (utente non privilegiato, PostgreSQL/
  pgvector nativi, nessun Ollama): **8/8 passed**, incluse le migrazioni
  fino alla 0011 e l'avvio/arresto puliti del worker.

## Comandi verificati (24 settembre 2026, chiusura P-05)

Ambiente: PostgreSQL 16.13 + pgvector 0.6.0 nativi (cluster temporaneo
`127.0.0.1:5433`, ruoli `newray_migrate`/`newray_app`/`newray_scheduler`
provisionati da `backend/scripts/db/bootstrap.sql` aggiornato). Nessun
Ollama/GPU in queste prove: il worker è provato con `ChatModel` fake
(claim/lease/fencing/checkpoint/deadline), non con inferenza reale — la
contesa GPU con processi esterni resta P-18/P-20.

- `.venv/bin/python -m pytest -q tests/unit tests/contracts tests/integration`
  da `backend/`: **400 passed**, 2 warning di terze parti (Starlette/httpx,
  AnyIO). Include i nuovi `test_run_worker.py` (5, fake),
  `test_http_durable_runs.py` (6, contratto HTTP) e
  `test_runs_worker_integration.py` (4, PostgreSQL reale: claim concorrente
  con esclusività della risorsa, checkpoint/finalize fencing-checked, reclaim
  di un worker verificato morto con "vecchio worker non può finalizzare",
  `count_active` scoped per organizzazione).
- `.venv/bin/ruff check src tests`, `.venv/bin/ruff format --check src tests`,
  `.venv/bin/mypy src` (74 sorgenti): verdi.
- `python scripts/check_architecture.py` (dal root): 74 file verificati, verde.
- `python scripts/generate_contracts.py --check` (dal root): nessun drift.
- `web`: `npm run check` (format/lint/typecheck/66 Vitest/licenze 640
  pacchetti/build/bundle 462,7 KB JS – 26,2 KB CSS): verde, nessuna UI nuova
  in questa chiusura. `npm run e2e` **non eseguito**: la revisione Playwright
  installata nell'ambiente (1194) non corrisponde a quella richiesta dal
  pacchetto (1243), limite dell'ambiente di sviluppo, non una regressione di
  questa modifica.
- `NEWRAY_LIVE_START_TEST=1 backend/.venv/bin/python scripts/tests/test_start_local.py`
  (eseguito come utente non privilegiato, PostgreSQL/pgvector nativi,
  nessun Ollama configurato): **8/8 passed**, incluso il worker: il test di
  smoke ora attende anche la sua liveness (`run_workers`) oltre a
  API/WebUI, verifica il riuso al secondo avvio (nessun secondo processo)
  e l'arresto pulito su Ctrl-C/riavvio del cluster.
- Verifica manuale supplementare di `./.start --web`: log conferma
  `Avvio worker run…` → `worker <id> pronto (risorsa gpu:0)` dopo le
  migrazioni e prima della WebUI, e all'arresto
  `arresto richiesto: termino il run in corso, poi esco` prima dello
  shutdown pulito dell'API.

## Comandi verificati (22 settembre 2026)

Ultimo gate backend: **323 passed, 2 skipped** con PostgreSQL 18.6 reale;
la sola suite integration conta **50 passed**. I due test live Ollama, eseguiti
separatamente con runtime 0.34.2 e digest dichiarato di `llama3.2:3b`, sono
**2 passed** e verificano anche la telemetria di generazione. Ruff lint/format,
mypy, checker architettura e codegen senza drift sono verdi. Il rapporto
completo è in
[backend-live-2026-09-22.md](../docs/evidence/backend-live-2026-09-22.md).

Le righe seguenti conservano anche le evidenze storiche del primo scheletro.

Riesecuzione B-03.2-11 del 20 settembre: suite offline 190 passed/43 skipped;
integrazione su cluster temporaneo PostgreSQL 18.6 + pgvector 0.8.6, 41 passed.
Ruff, format, mypy (57 sorgenti), codegen e architettura verdi; i numeri del
16 settembre sotto sono evidenze storiche, non la dimensione corrente.

- `uv lock` — risolve e aggiorna `uv.lock`
- `uv sync --locked` — installa l'ambiente dal lockfile (eseguito)
- `uv run ruff check .` e `uv run ruff format --check .` — pulito
- `uv run mypy` — pulito (56 file sorgente)
- `uv run pytest` — 119/119 offline (unit 61 + contracts 58, incluso il
  contratto dell'adapter Ollama su trasporto in memoria); i 2 test live
  (`tests/live`) sono opt-in: corrono solo con `NEWRAY_LIVE_OLLAMA_BASE_URL`,
  `NEWRAY_LIVE_OLLAMA_MODEL` e `NEWRAY_LIVE_OLLAMA_DIGEST`; con i DSN
  `NEWRAY_TEST_*_URL` di un'istanza reale, 146/146 inclusi i 27 integration
  su PostgreSQL (migrazioni, RLS, API)
- `psql -f scripts/db/bootstrap.sql` + `uv run alembic upgrade head` e
  `downgrade base` — eseguiti e verificati dai test di integrazione su
  PostgreSQL 18.6 + pgvector 0.8.6
- `uv run python -m newray.bootstrap.main` (A-07) — smoke live su PostgreSQL
  reale: bootstrap 201 + cookie, `/me` 200, revoca 204, `/me` post-rivoca
  401 `SESSION_INVALID`; bind non-loopback senza TLS rifiutato all'avvio
