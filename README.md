# NewRay / MyAssAgent 2.0

Assistente locale con interfaccia web e un solo modello generativo per run.
Il nuovo pilot punta a un unico Assistente con Gemma 4 31B IT da qualificare;
profili specialistici e selezione avanzata dei modelli sono successivi.
La specifica di prodotto è [NewRay.md](NewRay.md).

Questo repository contiene la specifica, le istruzioni di sviluppo e il
codice degli incrementi iniziali: stack bloccato, identità e sessioni,
PostgreSQL con migrazioni e RLS, API/UI collegate, contratti generati e CI
(vedi [docs/backlog.md](docs/backlog.md) per stati ed evidenze).

Il backlog storico registra sessione, conversazioni, profili/modelli e chat
inline nella WebUI; run e worker durevoli restano da consegnare. Il
[piano integrato funzioni–WebUI](docs/backlog.md#piano-integrazione-webui)
descrive i nuovi ticket e le prove del pilot. [todos.bak](todos.bak) conserva
il precedente backlog, congelato e non operativo.

## Orientamento

| Percorso | Responsabilità |
| --- | --- |
| [backend/](backend/README.md) | Backend Python, API FastAPI, dominio e worker |
| [web/](web/README.md) | WebUI React, TypeScript e Vite |
| [contracts/](contracts/README.md) | Contratti generati dai DTO del backend |
| [packs/](packs/README.md) | Profili, skills prodotto, cataloghi e template |
| [agent-skills/](agent-skills/) | Quattro skills per gli sviluppatori estratte dalla specifica |
| [scripts/](scripts/README.md) | Automazione di codegen, verifica e packaging |
| [deploy/](deploy/README.md) | Composizione dei servizi e configurazione operativa |
| [tests/](tests/README.md) | Percorsi end-to-end e prove live opt-in |
| [docs/](docs/README.md) | Architettura, ADR, criteri di accettazione e backlog |

L'[albero obiettivo](docs/architecture/structure.md) descrive anche i percorsi
che nasceranno con i relativi casi d'uso. La sezione 5 della specifica esclude
pacchetti vuoti e placeholder: non vengono creati moduli applicativi fittizi.

## Sviluppo

Su Linux, con uv, Node.js >=22/npm e PostgreSQL/pgvector installati:

```sh
./.start --web
```

Lo script prepara le dipendenze dai lockfile, genera le credenziali private,
inizializza e avvia un PostgreSQL dedicato, aggiorna lo schema e avvia API e
WebUI. Non occorre creare `.env` o inserire password. I servizi compatibili già
attivi vengono riutilizzati. La GUI sarà su `http://127.0.0.1:5173/` quando
compare «NewRay pronto»; `Ctrl-C` ferma soltanto i processi avviati da quel
comando. Il database e i dati persistono in `~/.local/share/newray/launcher`.

`.env` resta facoltativo per porte, dati, database esterni e Ollama: vedere
[.env.example](.env.example) e [guida runtime](docs/operations/runtime.md).
Ollama viene avviato se configurato; i modelli restano scelti esplicitamente.

Leggere [AGENTS.md](AGENTS.md), le istruzioni locali del componente interessato
e gli [ADR](docs/adr/README.md). [agent.md](agent.md) rimanda alle stesse regole.
Attività e avanzamento sono registrati soltanto in [docs/backlog.md](docs/backlog.md).

Stack scelto e bloccato: Python 3.12, FastAPI, PostgreSQL/pgvector,
SQLAlchemy/Alembic (`backend/uv.lock`); React/TypeScript/Vite nel browser
(`web/package-lock.json`). Ollama è il runtime locale dei modelli a partire
dall'incremento B. La CI è in `.github/workflows/ci.yml` (A-06).

La licenza del progetto resta da decidere prima della pubblicazione.
# virtualAssistent
