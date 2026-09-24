# ADR 0007 — Ruolo interno per il worker dei run durevoli

Data: 2026-09-24. Stato: adottato per P-05. Fonte: NewRay.md §§8.4, 19;
ADR 0001 (monolite modulare), ADR 0002 (PostgreSQL e scope obbligatorio).

## Contesto

Il worker dei run testuali durevoli (P-05) deve reclamare job in coda **di
qualunque organizzazione**, per una coda equa di sistema (NewRay.md §8.4:
"coda limitata ed equa fra utenti"). La tabella `runs` ha però RLS FORCE con
una policy scoped a `app.user_id`/`app.organization_id` di sessione
(migrazione 0009): un worker non agisce per conto di un singolo principal e
non può impostare quel contesto per vedere/reclamare righe di altri utenti.

ADR 0002 impone che "il ruolo DB applicativo... non ha superuser o
BYPASSRLS". ADR 0001 impone che "API e worker chiamano gli stessi casi
d'uso": non introduce un secondo stack, ma non vieta un secondo ruolo DB
interno per un'operazione di sistema ristretta.

## Decisione

Un ruolo `newray_scheduler` (NOLOGIN: nessuna sessione può autenticarvisi;
creato in `backend/scripts/db/bootstrap.sql` e nell'equivalente
`ensure_database` del launcher, fuori dalle migrazioni versionate) possiede
in via esclusiva quattro funzioni SQL `SECURITY DEFINER`
(`newray_claim_run`, `newray_heartbeat_run`, `newray_finalize_run`,
`newray_reclaim_stale_runs`, create dalla migrazione `0010`). Sono le uniche
operazioni che attraversano lo scope tra organizzazioni, e toccano solo le
colonne di gestione coda già concesse a `newray_app` in `0009` (state,
partial_text, finish_reason, contatori token/durata, lease_owner,
lease_until, fence, updated_at) — nessuna nuova superficie di scrittura,
nessun accesso a snapshot/idempotency_key/payload_hash al di fuori di
quanto già leggibile.

`newray_app` (usato sia dall'API sia dal processo worker, stesso ruolo,
coerente con ADR 0001) resta **senza BYPASSRLS**: riceve solo
`GRANT EXECUTE` sulle quattro funzioni, non un accesso diretto cross-scope
alle tabelle. `newray_migrate` diventa membro di `newray_scheduler` solo
per poter creare/riassegnare la proprietà delle funzioni dalle migrazioni
versionate (`ALTER FUNCTION ... OWNER TO newray_scheduler`); non acquisisce
altri privilegi e resta a sua volta soggetto a RLS FORCE sulle tabelle
utente, invariato.

`run_resource_leases` e `run_workers` (lease della risorsa GPU e liveness
del worker) non contengono dati di tenant — solo ID opachi di risorsa/
worker — e restano senza RLS, con grant diretti a `newray_app`.

## Conseguenze

ADR 0002 resta vera alla lettera per "il ruolo applicativo" (`newray_app`):
nessun BYPASSRLS, nessun accesso cross-org fuori dalle quattro funzioni
auditabili. L'eccezione è confinata, versionata (migrazione 0010,
reversibile in `downgrade`) e limitata alle sole operazioni di
claim/heartbeat/finalize/reclaim del worker durevole. Un ambiente di test
già predisposto prima di questa migrazione deve rieseguire
`backend/scripts/db/bootstrap.sql` (o l'equivalente creazione di ruolo) una
sola volta prima di applicare `0010`, perché la migrazione presuppone che
`newray_scheduler` esista già a livello di cluster.

Non introduce un secondo stack applicativo: le funzioni non contengono
logica di dominio, solo transizioni di stato/fencing sulle colonne già
autorizzate; l'orchestrazione (context, generazione, persistenza dei
messaggi) resta nel worker Python (`newray.modules.runs.worker`), che
chiama le stesse porte/casi d'uso dell'API.
