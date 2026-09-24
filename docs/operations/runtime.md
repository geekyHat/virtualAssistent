# Runtime e operazioni

Fonte: NewRay.md §§5.2, 20.3, 21. Stato A-07 (16 settembre 2026): avvio
nativo e compose minimi documentati e verificati; i servizi opzionali
(Ollama, parser, MCP, browser, search) nascono con gli incrementi che li
richiedono.

API (FastAPI/uvicorn) e PostgreSQL/pgvector costituiscono i servizi
applicativi di base. Ollama può essere locale o su endpoint interno
gestito; il browser passa sempre dalle API. Nessun flag di esposizione
remota senza TLS, autenticazione e configurazione esplicita (§20.3).

## Variabili d'ambiente lette dall'app (A-07)

| Variabile | Default | Scopo |
| --- | --- | --- |
| `NEWRAY_DATABASE_DSN` | generato da `.start`, obbligatorio per avvio API diretto | DSN del ruolo applicativo `newray_app`, mai quello delle migrazioni |
| `NEWRAY_BIND_ADDRESS` | `127.0.0.1` | Interfaccia di ascolto; non-loopback richiede `NEWRAY_COOKIE_SECURE=true` |
| `NEWRAY_PORT` | `8000` | Porta dell'API |
| `NEWRAY_COOKIE_SECURE` | `false` | `true` quando l'origine è servita in HTTPS (ufficio/produzione) |
| `NEWRAY_DATA_DIR` | `~/.local/share/newray` | Dati operativi fuori dai sorgenti (percorso assoluto) |
| `NEWRAY_MIGRATION_DATABASE_URL` | — | DSN del ruolo `newray_migrate`: letto da Alembic, mai dall'app |
| `NEWRAY_DATABASE_POOL_SIZE` | `5` | Connessioni massime per processo API, nessun overflow |
| `NEWRAY_DATABASE_POOL_TIMEOUT` | `3` | Attesa massima in secondi per una connessione libera |
| `NEWRAY_DATABASE_CONNECT_TIMEOUT` | `3` | Timeout connessione PostgreSQL in secondi |
| `NEWRAY_DATABASE_STATEMENT_TIMEOUT_MS` | `10000` | Limite per ogni statement SQL |
| `NEWRAY_DATABASE_LOCK_TIMEOUT_MS` | `3000` | Attesa lock, non superiore al limite statement |
| `NEWRAY_DATABASE_IDLE_TRANSACTION_TIMEOUT_MS` | `15000` | Termina sessioni con transazioni lasciate inattive |
| `NEWRAY_RUNS_MAX_QUEUE_DEPTH` | `50` | Limite di coda per scope (organizzazione/proprietario), P-05. Valore iniziale dichiarato, non misurato: da tarare in P-19/P-20 |
| `NEWRAY_RUN_MAX_DURATION_SECONDS` | `300` | Deadline complessiva di una generazione durevole (worker P-05). Valore iniziale dichiarato, non misurato |

Il processo API condivide un unico pool fra i moduli. Tutti i limiti sono
positivi e validati; zero non disabilita i timeout. Gli statement cancellati
rilasciano la transazione tramite rollback dell'adapter e la connessione è
riutilizzabile senza residui di scope. Questi sono budget DB distinti dalla
deadline complessiva del futuro run (B-04/B-05), non una deadline dell'intera
richiesta HTTP. Un thread SQL prosegue fino all'esito/timeout anche se il
consumer asincrono viene cancellato.

`NEWRAY_PUBLIC_ORIGIN` è letto dall'API e configurato dal launcher per la
WebUI. `NEWRAY_OLLAMA_BASE_URL` abilita il runtime opzionale; il launcher
avvia solo un endpoint locale `http://127.0.0.1:porta` se non raggiungibile.
`NEWRAY_CONFIG_FILE` resta una convenzione futura.

## Avvio nativo (sviluppo e produzione locale)

Prerequisiti Linux: uv, Node.js >=22/npm, PostgreSQL (`initdb`, `pg_ctl`,
`pg_config` nel PATH) e pgvector della stessa installazione. Non occorrono
Docker, sudo, ruoli precreati o variabili DSN. Dal repository:

```sh
./.start --web
```

Il launcher sincronizza il backend con `uv sync --locked` e prepara il web
con `npm ci --ignore-scripts` al primo avvio o quando cambia il lockfile.
Un `.env` facoltativo accetta assegnazioni letterali `NEWRAY_CHIAVE=valore`
(anche tra virgolette); non viene eseguito come shell. Gli export espliciti
prevalgono. `NEWRAY_ENV_FILE` può indicare un file alternativo (le prove usano
un file isolato e non leggono configurazioni personali). `NEWRAY_PORT` e
`NEWRAY_WEB_PORT` regolano API e WebUI insieme al
proxy e all'origine canonica; Vite non passa silenziosamente a un'altra porta.

Senza DSN, prepara un cluster privato in `NEWRAY_DATA_DIR/launcher/postgres`,
con credenziali casuali in `launcher/database.json` (0600) e autenticazione
SCRAM. La porta libera scelta fra 55432 e 55531 viene conservata. I ruoli
admin, migratore e applicativo sono separati: solo admin abilita pgvector,
app non possiede il database e non ha superuser/BYPASSRLS. Il cluster viene
riavviato se spento, senza rigenerare password né dati. Un lock impedisce
provisioning concorrenti. Un cluster senza il suo file credenziali richiede
il ripristino del file dal backup; non viene reinizializzato.

Con due DSN espliciti verifica il database indicato ed esegue le migrazioni;
quel servizio esterno va gestito con il suo gestore (Compose/systemd/remoto).
Non deduce un cluster da una porta e non sostituisce dati o password esistenti.
Se è impostato un solo DSN segnala la configurazione incompleta.

API e WebUI compatibili vengono riutilizzate. «NewRay pronto» richiede sia
l'HTML di questa WebUI sia il contratto API raggiungibile attraverso il proxy;
non basta una porta aperta. Le migrazioni mancanti richiedono l'arresto di
un'API preesistente. Ctrl-C termina soltanto i gruppi di processi creati dal
launcher; PostgreSQL resta disponibile e persistente. Nessun bootstrap di
identità viene eseguito automaticamente. Verifica di sola lettura:

Il launcher avvia anche il worker dei run durevoli (`python -m
newray.bootstrap.worker`, P-05), un solo processo per esecuzione: stesso
ruolo applicativo dell'API (`newray_app`), nessuna porta HTTP propria. La
sua readiness è la liveness registrata in `run_workers` (migrazione 0010),
non una risposta HTTP; viene riusato se già attivo con un heartbeat recente
e fermato da Ctrl-C come API/WebUI, lasciando terminare il run in corso
entro l'arresto del processo. `deploy/compose.yaml` non lo avvia ancora:
limite trasferito a P-18.

```sh
curl -i http://127.0.0.1:5173/api/v1/me # 401 prima del bootstrap nella WebUI
```

Ollama è opzionale. Se configurato e spento sul loopback IPv4, il launcher
richiama `scripts/serve_ollama_local.sh`, preservando l'isolamento GPU e senza
scaricare pesi né scegliere un modello. Un endpoint remoto non raggiungibile
produce un errore. La prova di avvio non qualifica l'inference dei modelli.

Per spegnere manualmente il solo cluster gestito (con API già fermata):
`pg_ctl -D /percorso/assoluto/newray/launcher/postgres -m fast -w stop`.
Il successivo `.start` lo riavvia. Non cancellare il cluster per correggere
errori di accesso: conservare credenziali e backup insieme.

## Avvio con Compose (Linux)

La composizione minima è `deploy/compose.yaml`: PostgreSQL/pgvector con
volume dedicato e API in host networking — il socket resta sull'interfaccia
loopback dell'host, senza bind non-loopback. Nessuna password predefinita:

```sh
cd deploy
cp .env.example .env
# impostare NEWRAY_DB_MIGRATE_PASSWORD e NEWRAY_DB_APP_PASSWORD
# con valori casuali (openssl rand -hex 16)
docker compose up -d --build
docker compose ps                        # db healthy, api in avvio
curl -sS 127.0.0.1:8000/api/v1/me        # 401 finché non c'è sessione
```

All'avvio l'entrypoint esegue `alembic upgrade head` come ruolo
proprietario e poi lancia l'API (idempotente: no-op se lo schema è già a
head). Il volume `newray-data` monta `NEWRAY_DATA_DIR` fuori dai sorgenti.

## Dati, backup e recupero

- Il database sta nel suo volume (`db-data` in compose, database dedicato
  nell'avvio nativo); i dati operativi stanno in `NEWRAY_DATA_DIR`, mai
  dentro i sorgenti o gli asset statici.
- Backup coordinati: `pg_dump` del database, contenuto di
  `NEWRAY_DATA_DIR`, la configurazione (`deploy/.env` e variabili) e le
  chiavi dei segreti, con prove di restore prima di usare dati non
  sintetici.
- Il rollback del codice non annulla una migrazione dati irreversibile:
  le migrazioni si scrivono per poter scendere o per essere
  accompagnate da un piano di recupero.
- La retention è da definire per ciascuna categoria con i rispettivi
  casi d'uso (incrementi successivi).

## Limiti (A-07)

- Compose, Dockerfile e entrypoint verificati in questo ambiente con
  `docker compose config` e con la sequenza di comandi replicata
  manualmente; il daemon Docker non è accessibile qui, quindi la prima
  `docker compose up` reale avviene sull'host di destinazione.
- Nessun reverse proxy né TLS: questa fase è il deployment loopback
  personale; l'edizione ufficio richiede TLS, autenticazione e
  configurazione esplicita (§20.3).
- Telemetria esterna disattiva e log minimizzati come da §21: nessun DSN,
  prompt o contenuto privato nei log.
