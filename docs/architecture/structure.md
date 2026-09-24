# Struttura del progetto

Fonte: [NewRay.md, sezione 5](../../NewRay.md#struttura).


Questo è l’albero obiettivo della sezione 5 di NewRay.md; non descrive file già implementati. Il presente
documento non richiede di crearle tutte subito: un modulo compare quando esiste
un caso d'uso reale. Evitare pacchetti vuoti e file placeholder.

```text
newray/
├── NewRay.md
├── AGENTS.md                         # Testo base nella sezione 24
├── README.md
├── LICENSE                          # Da decidere prima della pubblicazione
├── .gitignore
├── .github/workflows/                # CI, dipendenze e release
├── docs/
│   ├── adr/                         # Decisioni tecniche versionate
│   ├── product/                     # Scenari, ricerca utenti, criteri di utilità
│   ├── architecture/                # Confini, contratti e diagrammi
│   ├── security/                    # Threat model e matrice autorizzazioni
│   ├── operations/                  # Backup, recupero, aggiornamenti
│   ├── evidence/                    # Prove datate del candidato esatto
│   └── backlog.md                   # Unico ledger del nuovo progetto
├── backend/
│   ├── AGENTS.md
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── alembic.ini
│   ├── migrations/
│   ├── src/newray/
│   │   ├── bootstrap/
│   │   │   ├── api.py               # Crea app e middleware
│   │   │   ├── worker.py            # Avvia worker e risorse
│   │   │   ├── wiring.py            # Unico punto di composizione
│   │   │   └── settings.py          # Config risolta e validata
│   │   ├── kernel/
│   │   │   ├── identity.py          # Principal e ID
│   │   │   ├── errors.py            # Codici e tipi d'errore condivisi
│   │   │   └── clock.py             # Clock iniettabile
│   │   ├── modules/
│   │   │   ├── identity/
│   │   │   ├── access/
│   │   │   ├── conversations/
│   │   │   ├── runs/
│   │   │   ├── profiles/
│   │   │   ├── models/
│   │   │   ├── tools/
│   │   │   ├── extensions/
│   │   │   ├── skills/
│   │   │   ├── knowledge/
│   │   │   ├── memory/
│   │   │   ├── artifacts/
│   │   │   ├── web_research/
│   │   │   ├── mail/
│   │   │   ├── calendar/
│   │   │   ├── jobs/
│   │   │   ├── audit/
│   │   │   └── editions/
│   │   ├── interfaces/http/
│   │   │   ├── routes/              # Endpoint suddivisi per dominio
│   │   │   ├── dto/                 # Contratti Pydantic pubblici
│   │   │   ├── events.py            # Unione tipizzata degli eventi
│   │   │   ├── errors.py            # Mapping errori dominio → HTTP
│   │   │   └── middleware/          # Identità, limiti, request ID
│   │   └── infrastructure/
│   │       ├── database.py          # Pool e unità di lavoro; niente query business
│   │       ├── network.py           # Client ed egress controllati
│   │       ├── processes.py         # Avvio processi isolati
│   │       ├── secrets.py           # Interfaccia a secret store
│   │       └── observability.py     # Log e metriche minimizzati
│   └── tests/
│       ├── unit/
│       ├── contracts/
│       ├── integration/
│       ├── isolation/
│       └── fakes/
├── web/
│   ├── AGENTS.md
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── public/                      # Soltanto asset pubblici
│   └── src/
│       ├── app/                     # App, router, provider e layout
│       ├── pages/                   # Composizione di feature
│       ├── features/
│       │   ├── session/
│       │   ├── conversations/
│       │   ├── run/
│       │   ├── profile-selection/
│       │   ├── documents/
│       │   ├── artifacts/
│       │   ├── web-research/
│       │   ├── mail/
│       │   ├── calendar/
│       │   ├── approvals/
│       │   ├── memory/
│       │   ├── extensions/
│       │   ├── settings/
│       │   └── administration/
│       ├── shared/
│       │   ├── api/
│       │   ├── contracts/           # TypeScript generato
│       │   ├── ui/                  # Design system accessibile
│       │   ├── i18n/
│       │   ├── styles/
│       │   └── lib/                 # Utility piccole, senza business logic
│       └── test/                    # Setup e fixture comuni
├── contracts/                       # OpenAPI, JSON Schema, fixture generati
├── packs/
│   ├── profiles/                    # Assistant, Researcher, Coder
│   ├── skills/                      # Skills dell'assistente distribuito
│   ├── catalogs/                    # Modelli e integrazioni qualificati
│   └── templates/                   # Modelli di documento fidati
├── agent-skills/                    # Skills per gli sviluppatori, sezione 24
├── scripts/                         # Codegen, verifiche e packaging
├── deploy/                          # Compose iniziale, servizi e proxy
└── tests/
    ├── e2e/
    └── live/                        # Opt-in; corpus sintetici/autorizzati
```

### 5.1 Anatomia di un modulo backend

```text
modules/knowledge/
├── public.py                  # Tipi/casi d'uso accessibili agli altri moduli
├── domain.py                  # Entità, regole e stati
├── application.py             # Ingestion e retrieval come casi d'uso
├── ports.py                   # Repository, estrattore, embedder, indice
└── adapters/
    ├── postgres.py
    ├── docling.py
    └── local_files.py
```

Dividere `application.py` in un pacchetto solo quando ha responsabilità
separabili. Un adapter Ollama appartiene a `models/adapters/`; MCP a
`tools/adapters/`; Mem0 a `memory/adapters/`. I moduli consumatori importano
le porte pubbliche, non gli adapter. Evitare un enorme catalogo globale di
classi concrete e un nuovo file `orchestrator.py` che possiede tutto.

### 5.2 Runtime fuori dal codice

```text
NEW RAY DATA ROOT/                  # Percorso configurabile, mai servito staticamente
├── config/                        # Configurazione risolta; niente segreti nei pacchetti
├── imports/
│   ├── mcp/                       # Bundle/manifest appena importati
│   ├── skills/                    # Directory o archivi di skill
│   ├── models/                    # Manifest e artefatti da verificare
│   └── profiles/
├── packages/<kind>/<id>/<version>/ # Pacchetti immutabili dopo installazione
├── organizations/<org-id>/
│   ├── users/<user-id>/
│   │   ├── uploads/
│   │   ├── workspace/
│   │   └── artifacts/
│   └── collections/<collection-id>/
├── model-cache/                    # Solo staging; storage finale gestito da Ollama
├── temporary/<job-id>/
└── backups/                       # Destinazione operativa separabile
```

Usare nella configurazione il nome `NEWRAY_DATA_DIR`; il titolo dello schema
non è il nome letterale di una directory da creare. I dati PostgreSQL vivono
nel volume del servizio database. Il registro dei pacchetti e le attivazioni
vivono nel database; le cartelle non sostituiscono la verifica dei permessi.

