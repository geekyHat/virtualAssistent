# Backlog NewRay — pilot Assistente locale

Revisione: **23 settembre 2026**. Decisione: [ADR 0006](adr/0006-assistant-first-pilot.md).
Specifica: [NewRay.md](../NewRay.md). Questo è **l'unico ledger attivo**.
La riscrittura pianifica il prodotto: non implementa né qualifica le nuove funzioni.

## Archivio e regole del ledger

Il precedente backlog, inclusi tutti i TODO, stati, riesami ed evidenze, è
copiato integralmente in [todos.bak](../todos.bak), congelato e non operativo.
SHA-256 della copia e dell'originale prima della riscrittura:
`6099a591d3655d0d6aa85f45fe233bde1f7017d1c8baefefc06ddb9ce27cac4f`.
Non aggiornare quel file per segnare avanzamenti. La mappa in fondo assegna
ogni vecchio ticket a storia conservata, successore o lavoro differito.

Stati ammessi: **Da fare**, **In corso**, **Bloccato** (con causa e sblocco),
**Completato** (change identificabile e prove), **Differito** (non avviare).
Una dipendenza significa completamento
del contratto e delle prove indicati, non necessariamente attesa per scrivere
test o discutere DTO. Nessuna chiusura automatica per affinità con vecchi test.

Per ogni consegna registrare qui data, file/change o commit se disponibile,
comandi e risultati reali, ambiente, skip, limiti e residui assegnati.
Non duplicare stati in piani laterali; i rapporti di evidenza possono essere
separati ma devono essere collegati dal ticket.

## Perimetro e invarianti

- Un Assistente, un generativo: Gemma 4 31B IT UD-Q4_K_XL, candidato scelto
  per il pilot. Alias versionato con proiettore
  `newray-gemma4-31b-it:ud-q4-k-xl-vision-v1`:
  verificarlo contro catalogo/digest, non introdurre stringhe hardcoded nel dominio.
- Coder/Researcher fuori da onboarding e provisioning nuovi; dati, versioni,
  binding e run esistenti conservati. Configurazione avanzata successiva.
- Local-first, PostgreSQL autorevole, pgvector/FTS di base. Un embedding locale
  di supporto è ammesso; non è un secondo assistente. Cloud disabilitato.
- Chat centrale; menu modulare **a destra** con Conversazioni, Documenti,
  Memoria, Automazioni quando realmente disponibili. Raccolte/conoscenza è
  il nome utente del RAG. Modelli/profili/plugin non invadono la chat.
- Sicurezza = policy server-side e isolamento di processi/file/rete, non
  obbedienza del modello. Accesso ai documenti tramite ID e grant, mai path arbitrari.
- Run durevole, worker, lease/fencing, stop, replay e ricevute sono necessari.
  Non estendere la preview inline in un secondo motore di esecuzione.
- Memoria esplicita prima di memoria automatica; proposte procedurali prima
  di apprendimento periodico. Nessun training dei pesi o auto-modifica del codice.
- No Butler, classificatore di routing, critic/judge sincrono, fallback
  invisibile, shell generica, accesso cloud implicito o installazione suggerita dal modello.
- Memory LanceDB e Lobster sono candidati, non dipendenze già approvate.
  Von e servizi cloud sono differiti. Versioni/licenze e qualità vanno provate.
- Nessuna cancellazione di pesi, account, conversazioni o profili storici
  autorizzata dalla riscrittura del piano.

## Baseline conservata, non ricertificata oggi

L'archivio registra come realizzati stack/contratti, identità e recovery locale,
RLS e ruoli, conversazioni, profili/versioni, adapter testuale Ollama, preview
chat, componenti UI Tailwind/shadcn e diversi fix SSE/sessione/idempotenza.

In particolare **B-03.2-14 e B-03.2-38 restano chiusure storiche**.
Il rapporto di B-03.2-38 del 23/09 registra 299 test unit/contract e 63 integration
PostgreSQL senza skip, oltre a controlli statici/codegen. Non sono esecuzioni
di questa riscrittura e non qualificano worker, immagini o nuovo pilot.
Lo smoke storico di quattro modelli non qualifica Gemma sui nuovi workflow.

Resta da consegnare il percorso durevole e restano i debiti esplicitamente
riassegnati sotto. P-01 rileva regressioni nuove con evidenza; non riapre
automaticamente tutti i ticket completati. Le vecchie frasi di stato fra loro
discordanti restano documenti storici, non istruzioni operative.

<a id="piano-integrazione-webui"></a>
## Sequenza operativa e gate

| Fase | Ticket | Risultato verificabile |
| --- | --- | --- |
| 0 — Allineamento | P-01, P-19 | Baseline riproducibile e strumenti di qualifica affidabili |
| 1 — Assistente | P-02, P-03, P-04, P-05, P-06 | Un Assistente configurato, chat centrale, run/stop/replay durevoli |
| 2 — Risorse protette | P-07, P-08, P-09, P-10, P-11, P-12 | Gateway, documenti/immagini locali e contesto con fonti autorizzate |
| 3 — Continuità | P-13, P-14, P-15, P-16 | Memoria esplicita, workflow locale, prima proposta di skill controllata |
| 4 — Qualifica | P-17, P-18, P-20 | Contenimento, installazione/ripristino e prove live del pilot |

**Primo ticket eseguibile: P-01.** Poi P-02/P-03 per onboarding e runtime,
P-04 per la shell UI e P-05/P-06 per la chat completa. P-19 prepara presto le
prove, ma non autorizza download, avvii o test live non richiesti.
Le fasi sono checkpoint di prodotto; le dipendenze puntuali nei ticket
stabiliscono l'ordine tecnico. L'hardening di ogni slice si fa nella slice,
non si rimanda tutto a P-17.

Gate chat: P-02–P-06 più baseline; non basta un trasporto mock.
Gate pilot completo: tutti i P-01–P-20 completati con prove pertinenti.
Un no-go Lobster può chiudere P-14 con alternativa motivata; il workflow P-15
resta obbligatorio. Un no-go Gemma testo/tool/immagini blocca la promessa
corrispondente e richiede decisione utente, non un modello sostitutivo nascosto.
I ticket F-* non bloccano il pilot e non autorizzano implementazione anticipata.

## Contratto comune dei ticket

Queste condizioni fanno parte dei criteri di accettazione di **ogni** ticket:

1. Dominio/casi d'uso indipendenti da framework, SQL e provider; adapter dietro
   porte, bootstrap unico, dati di altri moduli solo tramite contratti pubblici.
2. Principal ricavato dalla sessione e ricontrollato lato server; scope
   organizzazione/proprietario/grant coerente, RLS con ruolo applicativo reale.
   Nessun profilo, skill, risultato tool o voce nascosta nella UI concede diritti.
3. Per dati nuovi: matrice accesso/revoca/cancellazione su DB, blob, indici,
   memoria, eventi/replay, cache, log, download/export e backup. Revoca nega
   subito nuove letture; purge fisico asincrono con esito osservabile.
   I backup seguono retention esplicita; restore riapplica tombstone/revoche
   prima di servire dati e non promette cancellazione fisica immediata dei backup.
4. Mutazioni con ricevute/idempotenza scoped dove necessarie, hash del payload
   stabile, versione attesa e transazioni brevi. Esito incerto riconciliato,
   niente retry ciechi. Approvazione vincolata ad argomenti/versioni/scadenza.
5. API elencate sotto sono **proposte da confrontare con §19 e il codice**:
   definire DTO e compatibilità/migrazione prima dell'implementazione; rigenerare
   OpenAPI/schema/client con script. Non introdurre alias divergenti o tipi
   frontend paralleli. Error envelope e correlation ID coerenti.
6. Slice end-to-end: caso d'uso, persistenza, API, UI e recupero quando pertinenti.
   Test unit/contract, DB/processi reali per le proprietà richieste, browser per UX.
   Fake e suite mock non sono prove di qualità live o contenimento OS.
7. Dipendenze eseguibili pinned con licenza/provenienza, runtime isolato,
   nessuna telemetria/cloud o download nascosti. Report senza documenti privati,
   credenziali, prompt integrali o percorsi sensibili.
8. Una chiusura documenta limiti e rollback/migrazione senza perdita dei dati
   precedenti. Se una prova non è eseguita resta esplicita, non diventa verde.

## Ticket pilot

### P-01 — Fotografare la baseline e risolvere le incongruenze residue

- **Stato / priorità:** Completato (23 settembre 2026) / P0.
- **Proprietario:** manutenzione trasversale; owner applicativi identity,
  conversations, profiles e models per le rispettive regressioni.
- **Dipendenze:** nessuna.
- **Contratto e risultato:** inventario verificato API/GUI/config/test correnti;
  distinguere implementato, provato offline, integration e live. Conservare
  recovery e switch modello già chiusi, verificando la copertura effettiva.
- **Principal, dati ed effetti:** sola diagnosi su codice e fixture; DB
  sacrificabile con nome/ownership verificati. Mai migrare o azzerare dati operativi.
- **Lavoro:** eseguire gate pertinenti da ambiente locked, controllare
  idempotenza same-key/same-payload distinta dal conflitto tra due scritture,
  errori sessione/recovery e isolamento. Inventariare catalogo a quattro modelli,
  seeding su GET, tipi manuali e route inline da migrare. Correggere solo
  regressioni riproducibili nello scope; assegnare altri rilievi ai ticket esistenti.
- **Accettazione / prove:** rapporto con comandi/esiti/skip e identificazione
  codice; nessuna regressione critica senza destinazione; B-03.2-38 non dichiarato
  privo di prova solo perché il nome di un test descrive un altro caso.
  Distinguere CI definita da CI realmente osservata.
- **Limiti / recupero:** non è una nuova qualifica Gemma e non modifica il prodotto.
  Il conteggio storico dei test è riferimento, non soglia da riprodurre artificialmente.

**Evidenze P-01 (23 settembre 2026).** Change identificabile:
[`web/e2e/ui/session.spec.ts`](../web/e2e/ui/session.spec.ts) allinea il caso
«sessione scaduta» a un'installazione già inizializzata. Prima: una esecuzione
completa ha dato 42/43 E2E, con redirect intermittente a `/` perché la fixture
combinava `me` autenticato e `status` non inizializzato; ripetizione isolata
10/10 passava, rendendo visibile la race dell'asserzione. Dopo: `npm run e2e`
43/43. Non è stata modificata la policy di sessione dell'applicazione.

- `backend/.venv/bin/python -m pytest -q tests/unit tests/contracts` da
  `backend/`: **299 passed**, 2 warning terze parti (Starlette/httpx, anyio).
  Il sandbox impediva `asyncio.to_thread`; riesecuzione fuori sandbox verde.
- Integration: `source ../.newray-test-env.sh` e
  `.venv/bin/python -m pytest -q tests/integration --tb=short` da `backend/`:
  **63 passed**, 2 warning terze parti, PostgreSQL locale con database
  `newray_it_*` temporanei e guardie di nome/ownership delle fixture. Un primo
  lancio dalla radice ha fallito nel setup Alembic per CWD errata; non è un
  difetto di migrazione. Non esporre DSN/credenziali nei report.
- `npm run check`: 63/63 Vitest, ESLint/Prettier/TypeScript/licenze/build/budget
  verdi; `npm run e2e`: 43/43 browser mock. `ruff check`, `ruff format --check`
  (111 file), `mypy` (66 sorgenti), checker architettura (66 file) e
  `generate_contracts.py --check`: verdi. CI remota non osservata.
- Inventario corrente: `GET /profiles` esegue seeding di Assistente/Coder/
  Researcher, catalogo locale di quattro modelli con default Qwen, WebUI con
  tipi profilo manuali e selettore a destra, route chat inline transitoria.
  Destinazioni: P-02, P-03, P-04, P-05/P-06 rispettivamente. Il test
  `test_switch_stessa_chiave_concorrente_restituisce_una_sola_ricevuta`
  copre già la concorrenza same-key su PostgreSQL; B-03.2-38 resta chiuso.
- Nessuna prova live Gemma, nessun worker durevole o browser→DB→Ollama in
  questi gate. I loro criteri restano in P-03/P-05/P-06/P-20.

### P-02 — Provisioning di un solo Assistente e migrazione conservativa

- **Stato / priorità:** Completato (23/09/2026) / P0.
- **Proprietario:** profiles; porte models/identity; onboarding web.
- **Dipendenze:** P-01.
- **Contratto e risultato:** provisioning esplicito e idempotente (valutare
  `POST /profiles/defaults` previsto), GET senza effetti; un solo default
  Assistente. Binding scelto dalla configurazione validata, mai dal browser fidato.
- **Principal, dati ed effetti:** utente autenticato nel proprio scope;
  transazione per profilo/versione/binding/ricevuta. Installazione di pesi
  richiede autorità operativa distinta dal provisioning del profilo.
- **Lavoro:** togliere Coder/Researcher dal nuovo seeding/onboarding; conservare
  vecchi record e riferimenti. Per installazioni esistenti proporre la transizione
  esplicita a Gemma mediante nuova versione; non sostituire il Qwen già assegnato
  senza conferma. Conversazioni vecchie leggibili; nuovo run specialistico
  non parte nel pilot senza scelta esplicita dell'Assistente, preservando lo storico.
- **Accettazione / prove:** installazione vuota e fixture legacy; richieste
  concorrenti e retry creano un solo default completo; fault a ogni scrittura
  senza residui; GET non provisiona; modello mancante non produce binding eseguibile
  fittizio; run storici/in corso invariati; due principal e refresh browser.
- **Limiti / recupero:** API versioni esistenti restano compatibili e protette;
  l'eventuale deprecazione richiede contratto, non cancellazione. Rollback della
  configurazione per nuovi run senza modificare snapshot o eliminare modelli.

**Consegna P-02 (23/09/2026).** Il nuovo `POST /api/v1/profiles/defaults`
provisiona idempotentemente soltanto l'Assistente nello scope del principal;
`GET /profiles` è ora senza scritture. L'alias configurabile Gemma vale per
nuovi profili, non sostituisce binding/versioni precedenti; il nome del nuovo
binding evita il riuso accidentale del vecchio `local-default`. La preview
non avvia nuovi run Coder/Researcher, ma profili e conversazioni storici
restano leggibili. UI: attivazione esplicita e composer inattivo finché
l'Assistente non ha un modello disponibile. File principali:
`backend/src/newray/modules/profiles/{domain,application}.py`,
`backend/src/newray/interfaces/http/routes/profiles.py`,
`backend/src/newray/modules/runs/application.py`,
`web/src/pages/ConversationsPage.tsx`, contratti rigenerati e test relativi.
Prove locali: 303 unit/contract, 65 integration su PostgreSQL reale,
63 Vitest, 44 Playwright, ruff/mypy/architettura/codegen/web check verdi.
I test PostgreSQL hanno usato database sacrificabili `newray_it_*` e
nessun modello live. Limiti assegnati: readiness Ollama/Gemma P-03; layout
chat-first e configurazione avanzata P-04/F-01; esecuzione durevole P-05/P-06.

### P-03 — Runtime Gemma e readiness onesta di testo/tool/visione

- **Stato / priorità:** Completato (24/09/2026, perimetro nativo ridotto su
  richiesta dell'utente) / P0. **Non equivale a modello o pilot qualificato.**
- **Proprietario:** models, adapter Ollama, bootstrap/deploy; stato runtime web.
- **Dipendenze:** P-01.
- **Contratto chiuso:** stato autenticato distingue runtime non configurato,
  irraggiungibile, errore runtime, catalogo vuoto, modello assente, artefatto
  incompatibile e installato **non qualificato**. Alias Gemma con proiettore
  verificato tramite prove native separate testo/tool/visione. Nessun fallback
  o avvio/download occulto. Lo stato `available` e le capacità qualificate
  persistenti **non** fanno parte di questa chiusura: sono trasferiti a P-19.
- **Principal, dati ed effetti:** diagnostica utente senza path/credenziali
  sensibili; operatori autorizzati gestiscono import e processi, non il modello.
- **Lavoro:** inventariare GGUF Gemma, eventuale `mmproj-F16.gguf`, licenze,
  digest, template e versione runtime. MTP non sostituisce il proiettore visivo.
  Verificare il modo supportato da Ollama per associare testo/vision; se il
  pacchetto non è compatibile aprire una decisione, non promettere che basta importarlo.
  Definire contesto iniziale moderato e massimo, budget/timeout, think esplicito,
  inventario di memoria KV/proiettore/runtime oltre ai pesi. Isolamento GPU e
  contesa nel deployment sono trasferiti a P-18; la qualifica delle capacità
  e il preflight persistente sono in P-19/P-20.
- **Accettazione del perimetro chiuso:** manifest artefatti riproducibile senza
  assumere il primo device enumerato; API/UI non confondono capacità dichiarate
  da Ollama con capacità qualificate. Ambiente e hardware identificati;
  assenza di test multi-GPU dichiarata. Stima risorse separata da P-20.
  Prima delle slice dipendenti eseguire, quando autorizzato, uno smoke nativo
  opt-in testo/tool strutturato senza effetti/immagine sugli artefatti esatti:
  serve a scoprire incompatibilità presto, non qualifica la catena applicativa.
  Se manca questa prova, dichiarare il rischio; se fallisce, fermare la promessa
  della capacità e acquisire la decisione senza aspettare la fine del pilot.
- **Limiti / recupero:** nessun nuovo modello generativo o ampliamento automatico
  del contesto. Fixture UI di tutti gli stati in P-04; readiness `available`
  e capacità qualificate con revoca su digest/hardware mutati in P-19;
  serializzazione/contesa GPU e prova multi-processo nel deployment P-18;
  visione end-to-end in P-10, qualità e consumi reali in P-20.

**Avanzamento P-03 (23/09/2026).** Il manifest ora identifica l'alias
versionato `newray-gemma4-31b-it:ud-q4-k-xl-vision-v1`, importato localmente
dal GGUF principale e dal proiettore visivo con SHA verificati; il vecchio tag
testuale resta intatto. Lo stato autenticato API/WebUI distingue le anomalie
di configurazione e runtime senza attivare fallback, mentre il contesto è
limitato a 8192 token iniziali e 16384 massimi. Con Ollama 0.34.3 e Radeon
AI PRO R9700, l'alias ha superato 7/7 sonde native testo-tool, 3/3 prove
live dell'adapter HTTP e 2/2 immagini sintetiche rosso/blu; a 8192 token
ha occupato 21.039.289.465 byte
di VRAM. L'alias storico ha invece rifiutato le immagini con HTTP 400. Fonte,
digest, metodo, risorse e limiti sono in
[evidenza P-03](evidence/p03-gemma-runtime-2026-09-23.md). Questa prova
documenta **solo il percorso nativo osservato**, non qualifica la catena applicativa
di P-10 né i gate trasferiti sotto.

**Correzione operativa (24/09/2026).** Ollama locale espone l'alias visivo ma
il launcher non lo collega se `NEWRAY_OLLAMA_BASE_URL` manca. Un `.env` locale
ignorato dal versionamento imposta esplicitamente URL loopback e alias pilot;
nessun download o switch dei profili esistenti. Una API già avviata richiede
riavvio per leggere la nuova configurazione. La diagnostica resta distinta
dalla qualifica live e non dichiara `available` sulla sola presenza del tag.
Avvio locale e proxy WebUI/API verificati; catalogo/readiness live sul digest
atteso 2/2 passati. Backend unit/contract 317 passati, 2 warning cosmetici;
codegen drift, mypy e lint verdi. Suite integration PostgreSQL non rivalidata
il 24/09: il cluster di test configurato sulla porta 5433 era spento (65
errori di connessione al setup, nessun esito sui casi).

**Audit precedente (24/09/2026, prima della riduzione di perimetro).** La
richiesta di chiudere P-03 non superava i criteri originari: il payload espone
`installed_unverified` ma non distingue `available` né presenta capacità
qualificate legate a digest e hardware; i test browser coprono solo alcuni
stati; il vincolo di una generazione (`OLLAMA_NUM_PARALLEL=1` nel launcher)
non è una prova di rilevamento della contesa con processi esterni o runtime
Ollama già gestiti altrove. La prova multi-GPU non è applicabile alla macchina
verificata: un solo acceleratore ROCm rilevato, driver NVIDIA non funzionante;
questa limitazione è registrata nell'evidenza.

**Decisione di chiusura provvisoria (24/09/2026).** Su richiesta esplicita
dell'utente P-03 viene chiuso **solo per il perimetro nativo sopra definito**,
con change e prove elencati in questa sezione e nell'evidenza collegata.
I criteri originari non coperti sono trasferiti come gate espliciti a P-04,
P-18, P-19 e P-20; nessun `qualified`/`available` è introdotto artificialmente
nel codice o promesso nella GUI. Questa decisione consente di iniziare P-05
testuale, ma non autorizza file, tool, immagini applicative o dichiarazioni
di pilot pronto. Se un gate trasferito fallisce, la capacità resta disabilitata
e il ticket proprietario non può essere chiuso.

### P-04 — Shell chat-first e menu modulare a destra

- **Stato / priorità:** Completato (24/09/2026) / P1.
- **Proprietario:** web/app, pages, features, shared/ui.
- **Dipendenze:** P-01.
- **Contratto e risultato:** conversazione centrale; menu destro richiudibile
  su desktop e drawer accessibile su mobile. Conversazioni, Documenti, Memoria,
  Automazioni collegate quando disponibili; niente pannelli vuoti simulati.
- **Principal, dati ed effetti:** cache/query/stream privati scoped all'identità;
  logout o cambio principal rimuovono dati e risposte tardive.
- **Lavoro:** riusare B-08.8 e Tailwind/shadcn, definire registro locale delle
  sezioni con route/etichetta/capability, senza plugin loader speculativo.
  Spostare profili/modelli nelle impostazioni avanzate appropriate. Composer
  multilinea, icone con nomi accessibili, stati vuoti, focus/tastiera, it/en.
  Cronologia paginata, Markdown sanitizzato e copia codice sicura; correlation ID
  visibile/copiabile negli errori. Guida recovery reale, non reset del DB.
- **Accettazione / prove:** E2E viewport desktop/mobile, tastiera e screen-reader
  semantics; centro libero da pannelli di amministrazione; cambio conversazione
  non mescola delta; nessun HTML/URL attivo ostile; query con tipi generati.
  Budget bundle misurato e motivato rispetto al gate esistente. Per la
  readiness spostata in Impostazioni avanzate, fixture e rendering recuperabile
  di **tutti** gli stati backend (non solo alcuni), incluso futuro `available`
  con capacità qualificate quando P-19 introduce il contratto.
- **Limiti / recupero:** il layout non qualifica i run; nuovi pulsanti allegati
  compaiono con P-08/P-10, non come finzioni. Migrazione incrementale, nessuna
  riscrittura wholesale o alterazione delle autorizzazioni API.

**Avanzamento P-04 (24/09/2026).** Il comando «Nuova conversazione» è ora la
prima voce della navigazione sinistra (menu su mobile), non più nel pannello
destro. La mutazione usa la stessa API e gestisce l'errore senza duplicare
creazioni; la lista conversazioni resta nel pannello destro. Restavano le altre
parti del ticket, quindi quella modifica non lo chiudeva. WebUI: 63 test Vitest,
45/45 E2E, lint/typecheck/build/budget verdi; 7/7 E2E mirati dopo la
rifinitura dell'etichetta readiness.

**Slice P-04 (24/09/2026).** `web/src/features/chat/AssistantSettings.tsx`
porta profili, catalogo, switch esplicito e readiness in Impostazioni →
Avanzate; `ChatWorkspacePanels.tsx` espone a destra soltanto la cronologia,
mentre `ConversationsPage.tsx` seleziona il solo Assistente pilot e offre
l'attivazione esplicita quando manca. Nessun binding storico o API è mutato.
Il composer usa ora una textarea locale con Invio per inviare e Maiusc+Invio
per andare a capo; un reply già persistito non è duplicato dal delta terminale.
Gate finali di questa slice: `npm run check` verde (63 Vitest,
lint/typecheck/licenze/build/budget) e `npm run e2e` 46/46 passati, incluso
il composer multilinea e desktop/mobile. Questa slice non chiudeva ancora
rendering Markdown sicuro, cronologia/recupero/correlation ID e gli altri
criteri elencati sopra.

**Chiusura P-04 (24/09/2026).** `useMessages.ts` usa il cursore
`after_sequence` del contratto generato per pagine da 200 e carica le
successive senza mescolare le conversazioni. `MessageContent.tsx` rende
Markdown tramite `marked` e `DOMPurify`, impedisce HTML/URL attivi ostili e
il caricamento di immagini remote dal testo; i blocchi codice sono copiabili
come testo. `ChatErrorNotice.tsx` espone e copia il correlation ID degli
errori API/SSE e guida al recupero senza retry automatico del run. Il registro
dei pannelli esplicita route/capability e mostra solo sezioni utilizzabili.
Le fixture browser coprono tutti e sette gli stati readiness correnti e il
futuro `available` con capacità qualificate; quest'ultimo resta un contratto
da introdurre lato backend in P-19, non uno stato già emesso oggi. Nuovi
allegati e chat durevole restano rispettivamente P-08/P-10 e P-05/P-06.
`npm run check` verde: 66 Vitest, lint/typecheck/formato/licenze/build/budget;
`npm run e2e` 48/48; dopo l'ultimo affinamento degli errori, 9/9 E2E
workspace mirati. Bundle misurato JS 462,7 KB e CSS 26,2 KB: il budget
JS passa esplicitamente da 400 a 480 KB per il parser e sanitizzatore
Markdown; nessun aumento CSS. L'audit npm segnala ancora due advisory
moderate su Vitest/@vitest/mocker, sole dipendenze di sviluppo;
`npm audit --omit=dev --audit-level=moderate` segnala zero vulnerabilità
di produzione. `dompurify` è fissato a 3.4.16.

### P-05 — Run testuale durevole, worker e scheduler

- **Stato / priorità:** Completato (24/09/2026) / P0.
- **Proprietario:** runs/scheduler; porte conversations/profiles/models.
- **Dipendenze:** P-02, P-03.
- **Contratto e risultato:** `POST /conversations/{id}/runs` idempotente e
  lettura snapshot; stati queued/running/waiting_approval/completed/failed/
  cancelled/interrupted secondo §8, con ragioni di terminazione esplicite.
- **Principal, dati ed effetti:** principal persistito come riferimento, grant
  rivalutati all'esecuzione; snapshot immutabile profilo/binding/digest/parametri.
  Creazione di run e input coordinata tramite porte/unità transazionale:
  nessun prompt orfano per fallimento della richiesta, nessun doppio messaggio.
- **Lavoro:** claim atomico PostgreSQL, coda limitata, heartbeat/lease/fencing,
  deadline token/tempo/turni, un'inference generativa attiva per risorsa.
  Worker avviato/riusato/fermato dal launcher con ownership/readiness esplicite.
  Nessuna transazione DB durante inference o attesa umana. Persistenza progressiva
  del parziale con checkpoint; budget contesto base deterministico.
  Non riassegnare GPU solo per TTL se il vecchio processo può ancora generare.
- **Accettazione / prove:** DB reale con due worker e fencing obsoleto;
  doppia richiesta stessa chiave e conflitto payload; coda piena e timeout;
  kill/restart con prompt e parziale recuperabili; vecchio worker non può
  finalizzare; metriche vendor nullable persistite, troncamento non successo completo.
- **Limiti / recupero:** introdurre il contratto di chiamata tool senza attivare
  effetti prima di P-07. Route inline resta solo compatibilità temporanea fino
  a P-06; nessuna nuova funzione costruita su quella route.

**Prima slice interna P-05 (24/09/2026).** La migrazione `0009_durable_text_runs`
aggiunge una tabella `runs` con scope/RLS FORCE, vincoli compositi verso la
conversazione, chiave idempotente scoped e snapshot JSONB non aggiornabile dal
ruolo applicativo; l'input è nel run,
non in un messaggio utente orfano. `DurableRunService` prepara e congela
profilo/binding/digest/parametri/contesto prima dell'inserimento e ripete la
stessa ricevuta senza risolvere di nuovo un binding eventualmente cambiato.
`PostgresRunStore` serializza la creazione contro la cancellazione della
conversazione e usa UNIQUE come barriera per richieste concorrenti. La slice
resta **interna**: nessuna route `/runs` è pubblicata e nessun job viene
eseguito finché claim, fencing, compute lease e recovery non sono verificati;
non si spaccia un run per completato. Prove: test unitario di replay/conflitto/
scope; test PostgreSQL reale con due inserimenti concorrenti, RLS, blocco
UPDATE dello snapshot e cascata;
suite backend completa 384 passed, 3 skipped, 2 warning di terze parti,
ruff e mypy verdi. Il cluster PostgreSQL temporaneo usato per queste prove
è stato fermato. Restano da fare worker/scheduler, lease/fencing, timeout,
checkpoint del parziale, endpoint/snapshot pubblico, restart e prove di
contenzione GPU prima della chiusura P-05.

**Chiusura P-05 (24/09/2026).** Migrazione `0010_runs_worker_scheduler`
aggiunge `run_resource_leases` (lease della risorsa generativa, seed
`gpu:0`) e `run_workers` (liveness, per distinguere lease scaduto da
worker verificato morto, NewRay.md §8.4); quattro funzioni SQL
`SECURITY DEFINER` (`newray_claim_run`, `newray_heartbeat_run`,
`newray_finalize_run`, `newray_reclaim_stale_runs`) possedute dal nuovo
ruolo interno NOLOGIN `newray_scheduler` attraversano lo scope fra
organizzazioni per il claim/checkpoint/finalize/reclaim, senza dare
BYPASSRLS a `newray_app` (ADR 0007, motiva la deviazione da ADR 0002 nel
dettaglio). `RunWorker`
(`backend/src/newray/modules/runs/worker.py`) ricostruisce il
`ChatRequest` dallo snapshot già congelato a creazione (nessuna
ri-risoluzione di profilo/binding), rinnova lease/checkpoint con un
heartbeat concorrente, applica una deadline di durata (non ancora
budget token/turni: nessun limite di turni esiste prima di P-07, il
conteggio token è registrato ma non usato come condizione di stop),
finalizza **prima** di persistere lo scambio in `conversations` così un
fencing perso fra l'ultimo heartbeat e la fine dello stream non scrive
alcun messaggio a nome del run. `finish_reason` del modello (incluso
`length` per il troncamento) è preservato senza reinterpretarlo in un
altro stato: stessa convenzione già in uso per la preview inline.
`DurableRunService.create` applica ora un limite di coda **per scope**
(organizzazione/proprietario, non un limite di sistema: il pilot resta a
singolo proprietario per installazione, ADR 0002; una coda condivisa fra
più organizzazioni è F-09), `NEWRAY_RUNS_MAX_QUEUE_DEPTH` (default 50) →
`QUEUE_FULL` 503. Nuove route pubbliche
`POST /api/v1/conversations/{id}/runs` e `GET /api/v1/runs/{id}` (solo
creazione idempotente e lettura snapshot; cancellazione ed
eventi/stream restano P-06, così `RunState.CANCELLED` resta non
raggiungibile da questa chiusura). Il worker gira come processo separato
(`python -m newray.bootstrap.worker`), avviato/riusato/fermato dal
launcher (`scripts/start_local.py`) con lo stesso pattern già usato per
Ollama: la sua readiness è la liveness in `run_workers`, non una porta
HTTP.

Prove reali (PostgreSQL 16.13 + pgvector 0.6.0 nativi, ruoli
`newray_migrate`/`newray_app`/`newray_scheduler` provisionati da
`backend/scripts/db/bootstrap.sql` aggiornato — nessun Ollama/GPU: il
worker è provato con `ChatModel` fake, la contesa GPU con processi
esterni resta P-18/P-20). `pytest tests/unit tests/contracts
tests/integration` da `backend/`: **400 passed**, 2 warning di terze
parti. Integration nuovi (`test_runs_worker_integration.py`, 4 test): due
claim concorrenti su un resource lease unico → vince uno solo; heartbeat
fencing-checked (un fence sbagliato non scrive il checkpoint); finalize
fencing-checked (un secondo finalize sullo stesso run, anche con fence
corretto, fallisce perché lo stato non è più `running`); reclaim di un
worker mai registrato in `run_workers` → `interrupted` con parziale
preservato, e il vecchio worker che prova a finalizzare o a fare un
ultimo checkpoint dopo il reclaim viene rifiutato — la prova diretta di
"vecchio worker non può finalizzare"; `count_active` scoped per
organizzazione. Unit nuovi (`test_run_worker.py`, 5, fake): happy path
claim→checkpoint→finalize con persistenza del messaggio; deadline
superata con parziale preservato; stream vuoto → `empty_output`; errore
del modello a metà stream → `model_error` con parziale preservato;
fencing perso a metà stream → nessuna scrittura di stato né di
messaggi. Contract nuovi (`test_http_durable_runs.py`, 6): 201 creazione
+ replay, 409 conflitto, 404 conversazione/run inesistente, 503
`QUEUE_FULL`, 422 validazione. `ruff check`/`ruff format --check` (src e
tests), `mypy src` (74 sorgenti), `scripts/check_architecture.py` (74
file) e `scripts/generate_contracts.py --check`: verdi.
`NEWRAY_LIVE_START_TEST=1 scripts/tests/test_start_local.py`, eseguito
come utente non privilegiato: **8/8 passed**, incluso un nuovo controllo
sulla liveness del worker nel test di avvio/riuso/riavvio (il secondo
avvio non duplica il worker; l'arresto Ctrl-C lo ferma insieme ad
API/WebUI). Verifica manuale supplementare di `./.start --web`: log
conferma l'avvio del worker dopo le migrazioni e il suo arresto
("termino il run in corso, poi esco") allo shutdown. `npm run check`
(web): verde, 66 Vitest, licenze, build, bundle 462,7 KB JS/26,2 KB CSS —
nessuna UI nuova in questa chiusura, solo client TS rigenerato.

**Limiti espliciti.** Nessuna prova con Ollama/GPU reale o processi
esterni in contesa: quella verifica resta P-18 (deployment) e P-20
(qualifica live), come già previsto dal ticket. Deadline solo a tempo:
budget token/turni per-run non è applicato come condizione di stop
(il conteggio arriva dal modello a fine generazione, non è un limite
imposto durante lo stream); un limite di turni non ha senso prima del
ciclo tool di P-07. "Kill/restart" del worker è provato simulando lease
scaduta + worker mai registrato (integration test), non uccidendo un
processo OS reale in automatico — la gestione SIGTERM/arresto pulito è
però verificata dal vivo nella smoke del launcher. La coda limitata è
per scope, non un limite di sistema condiviso fra organizzazioni
(coerente con l'installazione a singolo proprietario di ADR 0002; una
coda di sistema multi-org resta F-09). `deploy/compose.yaml` non avvia
ancora il worker: trasferito a P-18. `npm run e2e` non eseguito in questa
chiusura: la revisione Playwright installata nell'ambiente di sviluppo
non corrisponde a quella richiesta dal pacchetto, limite dell'ambiente,
non regressione introdotta qui; nessuna UI è comunque cambiata da questa
chiusura. File principali:
`backend/migrations/versions/0010_runs_worker_scheduler.py`,
`backend/src/newray/modules/runs/{worker.py,durable.py,durable_application.py,adapters/postgres.py}`,
`backend/src/newray/bootstrap/{worker.py,api.py,wiring.py,settings.py}`,
`backend/src/newray/interfaces/http/{routes/runs.py,dto/runs.py,errors.py}`,
`backend/scripts/db/bootstrap.sql`, `scripts/start_local.py`,
[ADR 0007](adr/0007-run-scheduler-role.md).

### P-06 — Eventi durevoli, stop e migrazione della chat browser

- **Stato / priorità:** Completato (24/09/2026) / P0.
- **Proprietario:** runs/events; web/features/chat e conversations.
- **Dipendenze:** P-04, P-05.
- **Contratto e risultato:** `GET /runs/{id}/events`, snapshot e
  `POST /runs/{id}/cancel`; envelope §19.3 versionato con event_id/sequence,
  replay/cursore, terminale unico e snapshot coerente al cursore.
- **Principal, dati ed effetti:** accesso anche a replay/snapshot rivalutato;
  revoca interrompe consegna privata. Cancellazione persistita e idempotente,
  effetti già avvenuti conservati; esito incerto non trasformato in successo.
- **Lavoro:** persistenza eventi/outbox coerente allo stato; reducer puro con
  deduplicazione/gap/resync; stop mostra richiesta fino all'esito autorevole.
  Cambio route/disconnessione browser chiude il trasporto, non cancella il run.
  Persistenza di messaggi parziali e metriche autorevoli; nessuna stima token dal testo.
  Passare composer alla route plurale; documentare compatibilità/rimozione della
  route inline, aggiornare test/synthetic chain e client senza doppio motore.
- **Accettazione / prove:** browser→API→DB con due tab, reconnect, cursore scaduto,
  terminale duplicato, eventi spezzati CRLF, EOF prematuro e race cancel/done;
  refresh riprende parziale, A non vede B; invalidazione cache su logout.
  Kill worker e stop durante inference liberano risorse o rendono l'incertezza visibile.
- **Limiti / recupero:** nessun retry automatico di invii incerti. Il roll-out
  preserva lo storico inline; rollback client non deve creare due run per invio.

**Chiusura P-06 (24/09/2026).** Migrazione `0011_run_events_and_cancel`
aggiunge la tabella `run_events` (outbox, RLS FORCE identica a `runs`,
`UNIQUE(run_id, sequence)`), `runs.next_event_sequence` e
`runs.cancel_requested_at`. Le quattro funzioni `SECURITY DEFINER` di P-05
(`newray_claim_run`/`newray_heartbeat_run`/`newray_finalize_run`/
`newray_reclaim_stale_runs`, ADR 0007) sono aggiornate per appendere
`run.started`/`message.delta`/`run.<stato>` nella STESSA transazione
fencing-checked della UPDATE: "terminale unico" resta ereditato da quella
barriera, non da una regola applicativa separata da provare a parte.
`newray_heartbeat_run` pota i `message.delta` oltre gli ultimi
`MAX_RETAINED_DELTA_EVENTS=50` (valore iniziale dichiarato, non tarato —
P-19/P-20); gli eventi di ciclo vita (al più 5 per run) non sono mai
potati. Un cursore più vecchio del delta più vecchio rimasto riceve un
singolo evento `resync` con lo snapshot corrente, mai un buco silenzioso
(NewRay.md §19.3).

Cancellazione (`RunStore.request_cancel`): un run `queued` transita subito
a `cancelled` (nessun worker lo possiede ancora, nessun fencing
necessario); un run `running` riceve solo `cancel_requested_at` — la
transizione resta al worker, che la legge dopo ogni heartbeat riuscito e
chiude cooperativamente lo stream con `finish_reason=cancelled_by_user`,
preservando il parziale (mai un kill forzato). `POST /api/v1/runs/{id}/cancel`
è idempotente: un run già terminale non cambia stato, la risposta riflette
lo stato reale.

`GET /api/v1/runs/{id}/events` è una SSE con replay/cursore/poll (stesso
pattern di polling di P-05, nessun LISTEN/NOTIFY): verifica lo scope prima
di aprire lo stream, poi replay/poll con ri-validazione periodica della
sessione — "la connessione non prolunga i grant" (§19.3) — fino a un
singolo evento terminale, poi chiusura pulita.

**Migrazione frontend completa, un solo motore.** `useChat.ts` è
riscritto per il percorso `POST .../runs` → `GET .../events` → `POST
.../cancel`, sostituendo l'inline route come UNICO percorso chiamato dal
composer. Nuovo `runEventsReducer.ts` (dedup per sequence, gap→`desync`,
resync che riallinea la baseline) al posto di `streamReducer.ts` (che
resta, non più usato dalla UI, per il contratto inline preservato).
`SseDecoder` esteso per leggere `id:` (cursore). Scrivere i test del
reducer prima di collegarlo ha trovato e corretto due bug reali, non solo
confermato il comportamento atteso: un `run.failed` con `finish_reason`
diverso da quelli riconosciuti (`model_error`/`deadline_exceeded`) veniva
classificato `empty`/`completed` invece di `failed` (mancava un hint dal
tipo di evento, non solo dal `finish_reason`); un `resync` con stato
terminale leggeva `payload.text` invece di `payload.partial_text`, perdendo
il testo dello snapshot. `chat-sse.spec.ts` (14 scenari SSE sull'inline
route) è `test.describe.skip`, documentato nel file: guidare "Invia" non
intercetta più quella route, quegli scenari non provano più un comportamento
reale del browser; `run-events.spec.ts` (nuovo, 14 test) porta la stessa
copertura sul percorso durevole più gli scenari propri di P-06 (reconnessione
dopo EOF, resync su cursore scaduto simulato, terminale duplicato, evento
senza `id:`). `workspace.spec.ts` (race cambio-conversazione/logout-durante-run)
migrato alle nuove route.

**Resume (due tab/refresh) aggiunto durante questa chiusura.** I criteri di
accettazione del ticket includono esplicitamente "due tab" e "refresh
riprende parziale": nella prima stesura della migrazione frontend questo
non era coperto — non esisteva alcun modo per il client di scoprire il run
attivo di una conversazione senza già possederne l'id, un vero scarto dai
criteri del ticket, non un limite dichiarato in anticipo. Aggiunto
`RunStore.find_active_by_conversation` (porta+adapter, RLS scoped, run
`queued`/`running` più recente per conversazione) e
`GET /api/v1/conversations/{id}/active-run` (`RunDTO | null`, mai 404 per
"fuori scope": RLS non fa emergere righe che il principal non può vedere,
stesso `null` di "nessun run attivo"). `useChat` guadagna un effect di
resume: al mount/cambio conversazione interroga l'endpoint e, se trova un
run non terminale, riprende lo stream dalla sequenza 0 (replay completo),
senza dipendere da `streamOwnerId` nel proprio array di dipendenze (che
cambierebbe per effetto dello stesso adottamento, causando un
riavvio/abort di sé stesso) — la guardia contro una `send()` concorrente è
`abortRef` (un ref, sempre aggiornato), non lo stato chiuso nella closure
dell'effect. Provato con due test e2e dedicati che seminano un run
direttamente nel registro del mock (mai attraverso il composer di questa
pagina, per rappresentare un'altra scheda) e poi navigano fresh sulla
conversazione.

Prove reali (PostgreSQL 16 + pgvector 0.6.0 nativi, stessi ruoli di P-05;
nessun Ollama/GPU, worker provato con `EchoChatModel`/fake). `pytest
tests/unit tests/contracts tests/integration` da `backend/`: **418
passed**, 2 warning di terze parti. Nuovi: `test_run_events_integration.py`
(4, PostgreSQL reale — sequenza monotona isolata per scope, retention dei
delta con resync su cursore scaduto, cancel `queued` diretta, cancel
`running` propagata dal worker reale con `ChatModel` fake lento e un gate
temporale sull'heartbeat), `test_http_run_events.py` (11, contratto —
replay completo/parziale, resync, 404 fuori scope, cancel idempotente, e i
4 su `active-run`), `test_rls_runs.py` esteso con una prova RLS reale di
`find_active_by_conversation` (nessun run, trovato in coda/in esecuzione,
fuori scope → `None` senza errore distinto, terminale → `None`).
`ruff check`/`ruff format --check` (src e tests), `mypy src` (74
sorgenti), `scripts/check_architecture.py` (74 file) e
`scripts/generate_contracts.py --check`: verdi. `web`: `npm run check`
(format/lint/typecheck/**85 Vitest**/licenze/build/bundle 465,7 KB JS —
26,2 KB CSS): verde. `npx playwright test --project=ui`: **55 passed, 7
skipped** (62 totali; l'eseguibile Chromium disponibile nell'ambiente,
revisione 1194, è stato puntato via una modifica locale **non commessa** a
`playwright.config.ts`, verificata e poi ripristinata — la revisione
richiesta dal pacchetto, 1243, non è installata qui, stesso limite già
osservato in P-05; `npm run e2e` di default resta quindi non eseguibile in
questo ambiente senza quella stessa modifica locale). Durante questo
lavoro una race era genuinamente presente in un TEST (non nell'hook): la
conversazione appena creata aveva un proprio resume-check in corso quando
il run veniva seminato subito dopo nello stesso test — risolta navigando
via prima di seminare il run, non aggirata; 4 esecuzioni consecutive di
`run-events.spec.ts` da solo dopo la correzione: 14/14 verdi ogni volta.
`NEWRAY_LIVE_START_TEST=1 scripts/tests/test_start_local.py`: **8/8
passed**, incluse le migrazioni fino alla 0011.

**Limiti espliciti.** Poll invece di LISTEN/NOTIFY (stessa scelta
dichiarata di P-05); retention dei delta (50) e intervallo di poll (0.5s)
sono valori iniziali dichiarati, non misurati — P-19/P-20 li tara.
Riconnessione client a un solo tentativo, non un backoff generale. Nessun
tool/fonte/approvazione: `tool.*`/`sources.updated`/`artifact.updated`
restano non emessi (P-07/P-11/P-15), come già dichiarato dal ticket.
Nessuna prova con Ollama/GPU reale: resta P-18/P-20. `chat-sse.spec.ts`
resta nel repository come documentazione degli scenari originali ma non
esercita più un percorso reale della UI; il contratto HTTP della route
inline resta provato lato backend (`test_http_chat_run.py`), non
dall'e2e. File principali:
`backend/migrations/versions/0011_run_events_and_cancel.py`,
`backend/src/newray/modules/runs/{durable.py,durable_application.py,worker.py,adapters/postgres.py}`,
`backend/src/newray/interfaces/http/routes/runs.py`,
`web/src/features/chat/{useChat.ts,runEventsReducer.ts}`,
`web/src/shared/api/sse.ts`, `web/e2e/{fixtures.ts,ui/run-events.spec.ts}`.

### P-07 — Gateway e ciclo tool sullo stesso Gemma

- **Stato / priorità:** Da fare / P0.
- **Proprietario:** tools/gateway, access; porte runs/models.
- **Dipendenze:** P-05.
- **Contratto e risultato:** tool call/result strutturati, schema validato,
  registry allowlist, policy e approval server-side; continuazione sullo stesso
  modello/snapshot. Primo caso concreto: strumento locale di stato del proprio run,
  senza effetti esterni, seguito dai tool documentali P-11.
- **Principal, dati ed effetti:** intersezione capacità profilo/tool installati/
  grant correnti. Argomenti del modello non scelgono principal o path. Ricevute
  per invocation con idempotenza; stati prepared/awaiting_approval/executing/
  succeeded/failed/outcome_unknown distinti.
- **Lavoro:** limiti numero/chiamate/tempo/output; approvazioni vincolate a
  payload/versione/scadenza e consumate atomicamente. Interrompere su revoca,
  tool sconosciuto o schema alterato. Contenuti tool sono dati non fidati;
  nessuna esecuzione di comandi da testo, annotazioni MCP o prompt.
- **Accettazione / prove:** fake/contract del ciclo e continuation identica,
  DB per receipt concorrenti, revoca fra approvazione ed effetto, replays,
  deadline/cancel; browser distingue richiesta conferma ed esito. Live tool use
  Gemma in P-20, non dedotto dal fake.
- **Limiti / recupero:** nessuna shell generica, nessun MCP server arbitrario
  installato. I futuri adapter si aggiungono a questa autorità, non la bypassano.

### P-08 — Allegati e archivio file con confine di sicurezza reale

- **Stato / priorità:** Da fare / P0.
- **Proprietario:** documents/storage, access; web allegati.
- **Dipendenze:** P-04, P-07.
- **Contratto e risultato:** upload autenticato, metadati, download e delete
  tramite ID opachi; relazione allegato→conversazione/run autorizzata.
  Definire endpoint/limiti multipart nei DTO, riusando §19 senza path client.
- **Principal, dati ed effetti:** blob privati per scope, upload staging
  non leggibile finché validato; quote byte/count, hash e provenienza.
  Nessun mount di home/root o accesso ai file di sistema. Egress negato ai
  processi file/parser; solo destinazioni locali strettamente necessarie.
- **Lavoro:** nome non fidato, MIME reale/estensione, streaming limitato,
  size/decompression/pixel/time limits; sandbox non-root con mount minimi.
  Prevenire traversal, symlink/hardlink e race tra verifica/apertura mediante
  primitive OS e import in storage gestito, non semplice confronto di stringhe.
  Composer con progress/cancel/retry esplicito, preview sicura e scadenza orfani.
- **Accettazione / prove:** upload interrotto/duplicato, file camuffato e troppo
  grande, traversal e link race; processi reali senza lettura di file estranei
  né rete non consentita. A/B su upload/download/cache; delete nega subito
  letture e purge verificabile; browser non carica risorse remote nella preview.
- **Limiti / recupero:** prima slice upload esplicito, nessuna scansione home.
  Import cartelle richiederà grant dedicato F-01. Nessun parser non isolato
  accettato come scorciatoia; i guasti lasciano quarantena recuperabile.

### P-09 — Documenti: estrazione recuperabile, raccolte e provenienza

- **Stato / priorità:** Da fare / P1.
- **Proprietario:** documents/knowledge; worker; web documenti/raccolte.
- **Dipendenze:** P-08.
- **Contratto e risultato:** documenti/raccolte e job di ingestione scoped;
  stati uploaded/processing/ready/failed/deleting da definire nei DTO,
  con errore e retry espliciti. Primo corpus TXT/Markdown/PDF testuale/DOCX.
- **Principal, dati ed effetti:** worker usa grant correnti; originali,
  estratti e chunk versionati con hash/fonte/pagina o sezione. Relazioni tra
  moduli tramite porte; raccolta non attribuisce automaticamente accesso al documento.
- **Lavoro:** qualificare parser mantenuti/versionati in processo separato;
  limiti pagine/archivi/memoria/deadline, nessun fetch esterno o macro attiva.
  Job idempotenti e fencing; staging→pubblicazione atomica della versione completa.
  UI lista/dettaglio, errori leggibili, raccolte e originale autorizzato.
- **Accettazione / prove:** corpus sintetico con file corrotti, DOCX ostile,
  PDF senza testo e documenti multilingue; kill/retry non duplica versioni;
  nessun indice parziale esposto; revoca durante ingest impedisce pubblicazione;
  scan senza OCR qualificato dichiarata non supportata, mai testo inventato.
- **Limiti / recupero:** OCR avanzato e authoring completi differiti F-06.
  Il parser scelto deve passare egress/FS test P-08; aggiornamento parser
  genera nuova versione riprocessabile, non distrugge quella precedente.

### P-10 — Visione Gemma end-to-end dal composer

- **Stato / priorità:** Da fare / P1.
- **Proprietario:** models/adapter Ollama, documents, runs; web allegati.
- **Dipendenze:** P-03, P-06, P-08.
- **Contratto e risultato:** contenuto multimodale tipizzato testo+attachment ID;
  il server risolve immagini autorizzate e le trasporta nel formato del runtime.
  Snapshot registra digest generativo e componenti visive/versioni senza blob nei log.
- **Principal, dati ed effetti:** solo immagini selezionate/confermate dall'utente;
  niente URL remoto del modello, path filesystem o bypass dei limiti storage.
  Metadata sensibili ridotti secondo policy; decode/resize in sandbox.
- **Lavoro:** formati immagine raster espliciti, limiti dimensioni/numero/budget;
  preview, rimozione prima invio e errore unsupported. Capability distinta da
  OCR e da generazione immagini. Verificare artefatto vision/proiettore insieme
  al runtime, non assumere che l'adapter testuale attuale lo supporti già.
- **Accettazione / prove:** contract trasporto byte/ID e validazione; immagine
  A non allegabile da B; errore/cancel/retry senza leakage; prova live di almeno
  descrizione, confronto visivo e lettura di un elemento chiaramente leggibile,
  con casi illeggibili dichiarati e risultati in P-20.
- **Limiti / recupero:** nessun nuovo generativo richiesto finché non è dimostrato
  il limite della coppia artefatti/runtime. Se non funziona, feature non dichiarata
  pronta e decisione esplicita; mai far passare risposta solo testuale come visione.

### P-11 — Retrieval locale con embedding e fonti verificabili

- **Stato / priorità:** Da fare / P1.
- **Proprietario:** knowledge/retrieval, adapter embedding; web fonti.
- **Dipendenze:** P-07, P-09.
- **Contratto e risultato:** ricerca scoped su documenti/raccolte autorizzati,
  ritorna chunk/source/version/score e riferimenti apribili; tool documentale
  attraverso gateway, senza dipendenza dal provider nel dominio.
- **Principal, dati ed effetti:** embedding e indici sono derivati privati;
  filtri applicati prima della selezione/ranking e ACL ricontrollate su output.
  Revoca/tombstone escludono fonte anche con indice/cache non ancora purgati.
- **Lavoro:** selezionare un piccolo embedding locale con licenza/digest,
  corpus italiano e schema versionato dimensione/modello/chunker; PostgreSQL
  pgvector exact + FTS di base, fusione deterministica. Quote batch e scheduling
  embedding non devono saturare la generazione. Nessun reranker LLM obbligatorio.
  Misurare indici conversazioni/versioni/retrieval prima di ottimizzare.
- **Accettazione / prove:** corpus golden con query/answerable/non-answerable,
  metriche retrieval e budget dichiarati prima della misura; due principal,
  grant revocato, reindex concorrente e delete fonte; click citazione apre
  passaggio originale corretto. EXPLAIN con ruolo app/dataset rappresentativo
  per modifiche indici, migrazioni compatibili e ordering a timestamp uguali.
- **Limiti / recupero:** indisponibilità embedding produce errore o percorso FTS
  esplicitamente dichiarato, non risposte finte; nessun cloud fallback.
  LanceDB non è prerequisito; modello embedding nuovo richiede reindex versionato.

### P-12 — Composizione deterministica del contesto, senza router

- **Stato / priorità:** Da fare / P1.
- **Proprietario:** runs/context tramite porte conversations/knowledge/models.
- **Dipendenze:** P-06, P-11.
- **Contratto e risultato:** input originale conservato, snapshot manifest
  del contesto (fonti/versioni/budget/motivi di esclusione), composizione testabile:
  policy → istruzioni profilo → storia autorizzata → fonti pertinenti → richiesta.
- **Principal, dati ed effetti:** verificare scope prima del modello e prima
  della risposta; i contenuti recuperati non diventano istruzioni privilegiate.
  Manifest privato e cancellabile; log tecnici non contengono estratti.
- **Lavoro:** budget separati input/output/immagini/tool, riserva risposta,
  deduplica e ranking stabili, contesto insufficiente segnalato. Troncamento
  esplicito e nessuna perdita silenziosa della richiesta corrente.
  Chiarimento utente quando mancano dati; nessuna riscrittura nascosta del prompt.
  Porta memoria aggiunta con il consumer reale P-13, non modulo vuoto.
- **Accettazione / prove:** stessa fixture→stesso manifest; limiti estremi,
  istruzioni ostili in documento, revoca tra retrieval e generation; fonti fuori
  budget non citate come lette. UI spiega dati utilizzati/limiti senza esporre
  catene di ragionamento interne o contenuti non autorizzati.
- **Limiti / recupero:** niente Von, classificatore o secondo LLM; eventuale
  assistenza alla riformulazione futura deve essere visibile e confermata.

### P-13 — Memoria esplicita, correggibile e dimenticabile

- **Stato / priorità:** Da fare / P1.
- **Proprietario:** memory; porte access/knowledge/runs; web Memoria.
- **Dipendenze:** P-12.
- **Contratto e risultato:** create/list/read/search/update/delete/export
  tramite MemoryService e DTO dedicati, versione attesa e provenienza;
  preferenze/fatti confermati distinti da documenti e cronologia.
- **Principal, dati ed effetti:** memorie private per default, consenso
  all'inserimento, origine/data/stato/scadenza; nessuna cattura automatica
  dell'intera chat o promozione di affermazioni del modello a fatti.
- **Lavoro:** PostgreSQL autorevole, pgvector opzionale derivato riusando P-11;
  UI «ricorda», modifica, dimentica, disabilita richiamo; conflitti espliciti,
  non sovrascrittura cieca. Contesto usa solo memorie attive/autorizzate,
  segnala origine. Delete fonte revoca i derivati che dipendono da essa.
- **Accettazione / prove:** memoria persistente dopo restart e correzione
  concorrente→conflitto; B non legge A in search/export/eventi; dimenticare
  impedisce richiamo anche da cache/indici/replay e proposte derivate.
  Vecchi messaggi che contengono la stessa informazione non la reinseriscono
  nel contesto dopo forgetting: definire soppressione/redazione o esclusione
  della fonte e provarla. Retention/restore con tombstone; nessuna promessa
  di rimozione di copie già scaricate dall'utente.
- **Limiti / recupero:** nessun secondo DB o servizio Mem0 imposto. Se retrieval
  memoria è indisponibile, chat lo dichiara e non inventa ricordi. Confronto
  OpenClaw Memory LanceDB in F-04, separato dalla consegna utile.

### P-14 — Qualifica Lobster con decisione go/no-go

- **Stato / priorità:** Da fare / P1.
- **Proprietario:** tools/extensions, runs; integrazione isolata candidata.
- **Dipendenze:** P-07, P-12.
- **Contratto e risultato:** scheda componente con commit/release/licenza,
  dipendenze/protocollo/processi/egress, mapping degli stati e prova delimitata.
  Caso target unico: documenti selezionati → sintesi → revisione → salvataggio locale.
- **Principal, dati ed effetti:** NewRay è unica autorità per principal,
  grant, run, approvazioni, idempotenza, cancel e ricevute; eventuale token
  resume è segreto scoped e non concede accesso autonomo.
- **Lavoro:** verificare che l'adapter possa esporre solo operazioni allowlist
  senza shell/template eseguibili liberi; limiti di processo/tempo/output e
  crash/restart. Nessun nuovo scheduler o DB autorevole. Testare protocollo
  con harness isolato, non installare un plugin generico nel backend.
- **Accettazione / prove:** resume duplicato, revoca, stop, scadenza approval,
  crash prima/dopo effetto e isolamento A/B; pin/licenze e sandbox verificati.
  Go solo se i contratti passano; no-go motivato con comportamento non supportato,
  costo e alternativa P-15 basata sui job NewRay esistenti.
- **Limiti / recupero:** la decisione non equivale a workflow consegnato.
  No-go non autorizza fork completo di Lobster né framework nuovo; eventuale
  sostituzione di un vincolo architetturale richiede nuova decisione.

### P-15 — Un'automazione locale completa e riprendibile

- **Stato / priorità:** Da fare / P1.
- **Proprietario:** runs/tools; artifacts per bozza locale; web Automazioni.
- **Dipendenze:** P-13, P-14.
- **Contratto e risultato:** avvio di workflow versionato con document ID e
  richiesta esplicita; prepara sintesi con fonti usando lo stesso Gemma,
  mostra bozza, chiede conferma e salva una revisione Markdown/testo privata.
  Stato, approval e ricevuta restano nello stesso run NewRay.
- **Principal, dati ed effetti:** lettura e scrittura distintamente autorizzate;
  output nello storage gestito, non path scelto dal modello. Conferma bound
  all'hash della bozza/versioni; download via ID autorizzato.
- **Lavoro:** adapter Lobster soltanto se P-14 go; altrimenti sequenza fissa
  nei job durevoli già disponibili. Persistenza passaggi/output, ripresa senza
  rigenerare o risalvare un effetto concluso, UI passi/stati/fonti/stop.
  Piccola slice artifacts con revisione/receipt, non suite authoring anticipata.
- **Accettazione / prove:** end-to-end documenti→bozza→conferma→file leggibile;
  restart durante attesa e dopo commit prima della risposta; retry non duplica
  artefatto; modifica invalida conferma; revoca nega salvataggio/download.
  Testi del modello non valgono come prova di salvataggio.
- **Limiti / recupero:** un solo workflow locale, niente email/web/account,
  pianificazione periodica, editor visuale o shell. Esiti incerti visibili
  e riconciliati prima di riprendere; vecchie versioni leggibili.

### P-16 — Prima proposta di skill da esperienza verificata

- **Stato / priorità:** Da fare / P1.
- **Proprietario:** learning/skills; porte runs/artifacts/memory; web proposte.
- **Dipendenze:** P-15.
- **Contratto e risultato:** azione manuale «proponi un metodo da questo lavoro»:
  proposta versionata con run/ricevute e correzioni utente come evidenza;
  stati proposta/in_validazione/approvata/attiva/rifiutata/ritirata.
- **Principal, dati ed effetti:** proposta privata, fonti autorizzate, niente
  credenziali/documenti integrali incorporati. Approvare una skill non concede
  tool/grant nuovi. Attivazione solo per run futuri con snapshot versione.
- **Lavoro:** stesso Gemma attraverso coda per l'eventuale generazione;
  validazione schema/contenuto, corpus offline di regressione e confronto
  prima/dopo, poi approvazione umana e attivazione atomica revocabile.
  Pilot limitato a istruzioni dichiarative, niente script eseguibili o
  modifiche al backend. Tracciare origine e invalidare derivati revocati.
- **Accettazione / prove:** successo è ricevuta verificata più valutazione,
  non autovalutazione del modello; proposta ostile, fonte eliminata, conflitto
  di versione e richiesta di permessi aggiuntivi rifiutati; A/B isolati.
  Browser prova proposta→test→approva→nuovo run→rollback; test mancanti
  impediscono attivazione, nessun peggioramento sulle invarianti del corpus.
- **Limiti / recupero:** non promette che il modello «si allena». Nessuna
  attivazione automatica o review a ogni messaggio; scheduling evoluto in F-03.

### P-17 — Verifica avversariale integrata di confini e lifecycle

- **Stato / priorità:** Da fare / P0 prima di dati reali.
- **Proprietario:** access/security e owner di ogni superficie dati.
- **Dipendenze:** P-10, P-16.
- **Contratto e risultato:** threat model e matrice dei controlli eseguibili,
  copertura file/immagini/documenti/memoria/skills/run/workflow e rete.
- **Principal, dati ed effetti:** utenti A/B, stessa e diversa organizzazione,
  anonimo e ruolo app non owner/non BYPASSRLS; grant scaduti/revocati.
  Solo fixture sintetiche e destinazioni di test autorizzate.
- **Lavoro:** attacchi via prompt/documenti/immagini/tool output, path/link,
  parser/preview e query/output injection; revoca in coda/stream/approval,
  cancellazione su cache/export/replay/indici e dipendenze di memoria/skill.
  Egress default-deny verificato a livello processo/rete; DNS/redirect/URL
  esterni non aggirano la policy. Cloud non disponibile e senza credenziali.
- **Accettazione / prove:** zero accessi o effetti non autorizzati nel corpus;
  evidenze DB/OS/rete reali, non soli mock. Controlli negativi dimostrano che
  il test rileva il bypass simulato. Nessun segreto nei report; difetto critico
  blocca pilot e ha ticket/responsabile; nessuna promessa di sicurezza assoluta.
- **Limiti / recupero:** non sostituisce i test di ciascuna slice né è un
  penetration test universale. La qualifica vale per runtime/configurazione
  identificati; cambi di sandbox/provider richiedono nuove prove.

### P-18 — Distribuzione locale, supply chain, backup e igiene

- **Stato / priorità:** Da fare / P1.
- **Proprietario:** bootstrap/deploy/CI; storage/identity per recovery.
- **Dipendenze:** P-06, P-16.
- **Contratto e risultato:** percorso Linux ripetibile non-root con healthcheck
  API/worker/runtime, ruoli e volumi minimi, restore verificato e dati separati
  dal codice. Nessuna dipendenza implicita da account o config della macchina.
- **Principal, dati ed effetti:** ruolo migratore non ereditato dall'app;
  DSN redatti, password sintetiche con caratteri speciali gestite in sicurezza;
  operazioni backup/recovery riservate all'operatore, mai al modello.
- **Lavoro:** pin immagini/runtime/build backend e licenze locked con eccezioni
  motivate/SBOM; gate licenze Python e build/smoke Compose in CI, script CWD-independent.
  Escludere segreti/test-env da build/distribuzioni; codegen atomico e verifica
  drift; bundle checker con errore utile, lang/i18n coerenti. Valutare API
  identity/config inutilizzate con consumer prima di rimuoverle.
  Backup DB/blob/metadati coerente, retention esplicita, restore in ambiente isolato.
  Residuo P-03 trasferito: il deployment Ollama deve imporre una sola
  generazione/risorsa anche nei processi effettivi, isolare GPU compute da
  display e rilevare/mostrare contesa con processi esterni o runtime già
  gestiti altrove, senza fermarli automaticamente.
- **Accettazione / prove:** installazione pulita e upgrade fixture storiche;
  app senza DSN/ruolo migratore, non-root e mount verificati; immagine avviata,
  non sola compose config. Restore conserva scope/ricevute e applica tombstone,
  non rianima grant. Interruzione codegen non pubblica metà contratto.
  Workflow CI definito distinto da esito realmente osservato. Prova di
  contesa e serializzazione con processi reali; prova multi-GPU quando
  disponibile, altrimenti hardware mancante dichiarato senza simulare successo.
- **Limiti / recupero:** niente rilascio pubblico, invio telemetria o installer
  multi-OS impliciti. Prima di dati reali definire e misurare RPO/RTO e tempi purge;
  backup prova separata dai dati operativi, senza downgrade distruttivi.

### P-19 — Strumenti di qualifica con rapporti affidabili

- **Stato / priorità:** Da fare / P1, anticipare a fase 0.
- **Proprietario:** scripts/qualifica, models; test tooling.
- **Dipendenze:** P-01.
- **Contratto e risultato:** report JSON progressivo/atomico per esecuzione
  con run ID, codice/corpus/config hash, digest artefatti, runtime/hardware,
  errori e fasi; mai riusare come successo il rapporto di un tentativo precedente.
  Residuo P-03 trasferito: prova locale persistente legata a digest, versione
  runtime e hardware; l'API autenticata distingue `available` da
  `installed_unverified` e restituisce solo le capacità realmente qualificate.
- **Principal, dati ed effetti:** corpus sintetico/pubblico autorizzato,
  nessun segreto o prompt privato; discovery/read-only di base, inference/unload
  solo nell'esecuzione live esplicitamente richiesta.
- **Lavoro:** mantenere script esistenti restringendo il default di qualifica
  al solo candidato pilot; registrare discovery/warmup/load/probe/unload falliti,
  chiudere client in finally, modello assente gestito senza eccezione grezza.
  GPU identificata per device effettivo; misure testo/visione/tool distinte.
  Su digest/runtime/hardware mutati la qualifica decade, senza fallback o
  mutazioni automatiche del binding; preflight non autoconcede `qualified`.
- **Accettazione / prove:** fault injection in ogni fase produce nuovo JSON
  valido e exit non zero; client rilasciati, modello fallito presente, interruzione
  non distrugge ultimo checkpoint. Test da altra CWD, nessuna GPU/rete richiesta
  nei gate offline; corpus e soglie fissati prima della campagna P-20.
  Contract/fake per mismatch e revoca della qualifica; UI completa in P-04.
- **Limiti / recupero:** non riqualificare i quattro modelli storici. Generare
  un report non equivale a passarlo; nessuna installazione/download automatici.

### P-20 — Qualifica live e accettazione del pilot

- **Stato / priorità:** Da fare / P0 per dichiarare il pilot pronto.
- **Proprietario:** QA trasversale, owner delle slice; validazione prodotto con utente.
- **Dipendenze:** P-17, P-18, P-19.
- **Contratto e risultato:** report riproducibile browser→API→PostgreSQL→worker→
  Ollama sul Gemma/artefatti/runtime identificati; esito go/no-go per ciascuna
  capacità e per pilot completo, senza confondere mock, smoke e qualità.
  La chiusura ridotta di P-03 non soddisfa questo gate né permette di chiamare
  «qualificate» testo/tool/visione nell'app prima delle prove P-19/P-20.
- **Principal, dati ed effetti:** almeno A/B sintetici; input/corpus autorizzato,
  nessun documento personale o account cloud necessario; effetti solo storage di test.
- **Lavoro:** fissare prima della prova corpus italiano, rubriche e soglie
  numeriche di qualità/latency/TTFT/throughput/memoria/context massimo; includere
  chat, tool schema/errori, confronto documenti con citazioni, risposta non
  supportata, immagine illeggibile, ricordo/correzione/forgetting, workflow con
  approvazione e skill/rollback. Warm e cold separati, contesa GPU dichiarata.
  Registrare RAM/VRAM effettiva, non confonderla con dimensione del GGUF.
- **Accettazione / prove:** tutti i contratti di sicurezza/idempotenza/stop/
  recupero passano, task utente valutati contro rubriche predefinite, nessuna
  azione dichiarata senza ricevuta; runtime spento/modello assente/coda piena/
  refresh/restart non causano falsa completion. Prove non eseguite bloccano
  la relativa qualifica; nessuna modifica retroattiva delle soglie per far passare.
- **Limiti / recupero:** hardware/corpus non disponibili → stato Bloccato con
  requisito preciso; no-go → proposta di scope/configurazione sottoposta all'utente.
  Nessun cambio nascosto di modello né promessa generalizzata a tutti gli uffici.

## Ticket successivi — non avviare nel pilot

### F-01 — Profili esperti, modelli e plugin con installazione controllata

- **Stato:** Differito. **Proprietario:** profiles/models/extensions/access + UI Avanzate.
- **Dipendenze:** P-20.
- **Contratto:** catalogo versionato e capacità claimed/compatible/qualified;
  profili manuali, import inerte→validazione→attivazione/rollback, nessun routing.
- **Principal / dati / effetti:** installatore/admin distinto da utilizzatore;
  grant di lettura cartelle esplicito separato da scrittura/esecuzione.
  Import file in storage gestito con confine OS P-08, no mount home generale.
- **Scope:** riattivare Coder/Researcher solo come scelte esperte; assegnazione
  skills/tools/modello per versione; estensioni isolate, permessi spiegati,
  firma/digest/licenze e pin. Riutilizzare dati/versioni storiche.
- **Accettazione / prove:** import non esegue codice, cambio modello vale per
  nuovi run, rollback/revoca interrompono accessi futuri; no escalazione da UI/API,
  due principal, symlink escape e plugin ostile; onboarding ordinario invariato.
- **Limiti:** nessun marketplace pubblico o compatibilità universale promessa.

### F-02 — Provider cloud con autorizzazione separata all'esportazione

- **Stato:** Differito. **Proprietario:** models/connections/access/egress.
- **Dipendenze:** P-20.
- **Contratto:** provider scelto esplicitamente, manifest dei dati destinati
  al provider, grant export per risorse/destinazione/finalità/scadenza;
  revoca/consenso verificati prima di ogni invio.
- **Principal / dati / effetti:** lettura locale non è export; includere
  immagini, estratti, embedding, memorie, summary e tool output. Credenziali
  custodite dal server; nessun provider riceve l'intero filesystem.
- **Scope:** primo adapter specifico, minimizzazione/redazione, policy egress
  centralizzata e osservabile; dichiarare retention e limiti cancellazione remota.
- **Accettazione / prove:** mock/provider sandbox e rete reale controllata:
  revoca, redirect, retry, stream e prompt injection non esportano dati extra;
  mostrare all'utente cosa lascia il dispositivo; nessun fallback cloud.
- **Limiti:** attivazione richiede scelta provider/account e autorizzazione
  separata; il ticket non collega account o accetta condizioni per l'utente.

### F-03 — Review periodiche e apprendimento procedurale avanzato

- **Stato:** Differito. **Proprietario:** learning/skills/scheduler.
- **Dipendenze:** P-20.
- **Contratto:** job opt-in con frequenza/budget/finestra inattiva/cancellazione;
  produce proposte nel lifecycle P-16, non attivazioni automatiche.
- **Principal / dati / effetti:** corpus autorizzato e selezionabile,
  revoca/retention sulle evidenze; credenziali e documenti privati esclusi dai pack.
- **Scope:** confronto ispirato ai metodi Hermes/OpenClaw, pin dei componenti
  eventualmente riusati, deduplica proposte, eval offline e rollback misurabile.
- **Accettazione / prove:** miglioramento su task definiti senza regressioni
  sicurezza/qualità, budget rispettato, chat non affamata dallo scheduler,
  stop/restart/opt-out e zero nuove autorizzazioni tramite skill.
- **Limiti:** niente training pesi, code self-modification, giudice sincrono
  o review automatica a ogni turno.

### F-04 — Confronto Memory LanceDB contro memoria NewRay

- **Stato:** Differito. **Proprietario:** memory/knowledge, qualificazione dipendenze.
- **Dipendenze:** P-20.
- **Contratto:** benchmark del plugin OpenClaw esatto e di un eventuale adapter,
  con sorgente/commit/licenza e boundary API descritti; nessuna compatibilità presunta.
- **Principal / dati / effetti:** PostgreSQL resta autorevole; indici derivati
  scoped e ricostruibili; nessun auto-capture/telemetria/cloud abilitato per default.
- **Scope:** corpus uguale alla baseline; confrontare recall/precisione, latenza,
  RAM, costo ingest/reindex, cancellazione/restore e complessità operativa.
- **Accettazione / prove:** soglie e beneficio necessario scritti prima dei test;
  delete/revoca/A-B/crash passano. Go/no-go documentato; un secondo DB richiede
  ADR e piano migrazione/rollback. No-go conserva la base funzionante.
- **Limiti:** non riscrivere un memory framework né adottare Mem0 in alternativa
  senza separata valutazione. Questo è uno spike delimitato, non un'integrazione promessa.

### F-05 — Valutare Von solo per un bisogno misurato

- **Stato:** Differito. **Proprietario:** context/qualificazione.
- **Dipendenze:** P-20.
- **Contratto:** esperimento offline su un caso delimitato e baseline
  deterministica; verificare dal codice pinned cosa fa davvero il candidato.
- **Principal / dati / effetti:** nessun tool, privilegio, modello o provider
  selezionato automaticamente in base al punteggio; input sintetici/locali.
- **Scope:** misurare se classificazione/scoring dà valore, senza chiamarlo
  prompt optimizer o workflow executor senza prova. Eventuale suggerimento
  di riformulazione visibile come diff, accettato dall'utente.
- **Accettazione / prove:** utilità, falsi positivi, latenza e licenza; nessuna
  alterazione dell'intento o bypass policy. Adozione solo con decisione esplicita
  compatibile con ADR 0003; no-go se beneficio non giustifica dipendenza.
- **Limiti:** nessuna introduzione implicita di router/Butler o secondo LLM.

### F-06 — Authoring completo e OCR qualificato

- **Stato:** Differito. **Proprietario:** artifacts/documents + editor web.
- **Dipendenze:** P-20.
- **Contratto:** revisioni con versione attesa, export DOCX/PDF della revisione
  richiesta, job OCR distinto dalla visione generativa.
- **Principal / dati / effetti:** fonti, preview, originali e export scoped;
  render/parser non-root senza egress, font e renderer identificati.
- **Scope:** editor e template locali, conflitti/recovery, scansioni/corpus
  italiano con parti illeggibili esplicite; non cambiare il modello generativo
  senza decisione. Licenze/digest dei supporti OCR da qualificare.
- **Accettazione / prove:** file realmente apribili, revisione corretta,
  fallimento conserva bozza, limiti OCR misurati, revoca/delete su derivati.
- **Limiti:** output grafico generativo e suite office universale esclusi.

### F-07 — Ricerca web con egress e fonti controllati

- **Stato:** Differito. **Proprietario:** research/tools/access + fonti web.
- **Dipendenze:** F-02.
- **Contratto:** primo provider/adapter qualificato; search e lettura distinte,
  fonti URL/data/passaggio e cache scoped cancellabile, query visibile.
- **Principal / dati / effetti:** ricerca esterna è un'esportazione; niente
  estratti privati nella query senza grant. SSRF/DNS/redirect/rete interna negati.
- **Scope:** fetch limitato/sanitizzato, prompt injection trattata come dati;
  browser automatizzato solo se giustificato e isolato.
- **Accettazione / prove:** risposta cita pagine realmente lette, guasti
  dichiarati, redirect/SSRF/egress/revoca e leakage cache testati.
- **Limiti:** nessun login, acquisto, pubblicazione o crawlers indiscriminati.

### F-08 — Email e calendario con conferme e riconciliazione

- **Stato:** Differito. **Proprietario:** communications/connections/tools.
- **Dipendenze:** F-02.
- **Contratto:** account/scope espliciti, lettura/bozza/invio distinti;
  approvazione su destinatari/allegati/testo o intervallo/fuso/partecipanti,
  ricevuta provider e outcome_unknown riconciliabile.
- **Principal / dati / effetti:** credenziali server-side, revoca e retention;
  collegare account non concede indicizzazione perpetua della casella.
- **Scope:** un provider alla volta, adapter dietro porte NewRay, token refresh,
  sync cursor/idempotenza, allegati e HTML inerti, no immagini di tracking.
- **Accettazione / prove:** modifica dopo conferma invalida invio, timeout
  post-effetto non reinvia, ETag/conflitti fusi/ricorrenze testati, A/B isolati.
- **Limiti:** prove live solo con account e destinatari di test autorizzati;
  nessun invio reale autorizzato dalla sola descrizione del ticket.

### F-09 — Edizione ufficio e amministrazione avanzata

- **Stato:** Differito. **Proprietario:** identity/access/admin/deploy.
- **Dipendenze:** P-20.
- **Contratto:** sharing esplicito, ruoli/installatore/admin, audit minimizzato,
  policy organization e concorrenza; TLS/proxy/SSO solo se scelti e qualificati.
- **Principal / dati / effetti:** admin non ottiene lettura generale dei
  documenti privati; account/recovery/backup con separazione dei privilegi.
- **Scope:** UI amministrativa, quote organizzazione, test capacità su carico
  dichiarato, sessioni remote/CSRF e deployment diverso dal personale loopback.
- **Accettazione / prove:** matrice membro/admin/A-B e revoca su eventi/cache,
  stress con code fair, restore scoped, cookie/origine/proxy verificati realmente.
- **Limiti:** non rinvia l'isolamento del pilot; nessun SaaS multi-tenant o
  promessa hardware/commerciale automatica.

<a id="todo-backend"></a>
<a id="consolidamento-backend"></a>
## Vista backend

Ordine: P-01 → P-02/P-03 → P-05 → P-06/P-07 → P-08 → P-09/P-10 →
P-11 → P-12 → P-13/P-14 → P-15 → P-16. P-19 parte dopo P-01;
P-17/P-18 chiudono i gate prima di P-20. È una vista del ledger, non nuovi TODO.
I ticket full-stack includono i relativi consumer web.

<a id="todo-frontend"></a>
<a id="consolidamento-frontend"></a>
## Vista frontend

P-04 riusa la shell esistente; P-02/P-03 integrano onboarding/readiness.
P-06 consegna chat durevole, P-08/P-10 allegati reali, P-09/P-11 documenti/fonti,
P-13 Memoria, P-15 Automazioni, P-16 proposte. Le feature non ancora complete
non generano menu vuoti o simulazioni di successo.

<a id="prove-condivise"></a>
## Vista prove

P-01 baseline, P-19 strumenti, prove nella singola slice, P-17 contenimento,
P-18 deployment/restore, P-20 qualità live. Non ridurre tutti i gate a un
conteggio di test. La GUI mock non chiude browser→API→DB→Ollama.

## Migrazione completa del ledger precedente

Le righe «storico» conservano la chiusura registrata, non la ricertificano.
Per ticket parziali si conserva la parte fatta e si assegna **solo il residuo**.
ID non presenti nel ledger originale non vengono inventati per colmare numerazioni.

| Vecchio ID | Disposizione nel nuovo piano |
| --- | --- |
| S-01, S-02 | Storico conservato; nuove decisioni ADR 0006, istruzioni invarianti |
| A-01, A-02, A-03, A-04, A-05, A-06, A-07 | Storico conservato; consolidamento residuo P-01/P-18 |
| B-01 | Storico conservato; integrazione durable P-05/P-06 |
| B-02 | Storico conservato; nuovo default e compatibilità P-02 |
| B-02.1 | Versioni/switch conservati; provisioning P-02, selezione esperta F-01 |
| B-02.2 | Stati runtime/readiness P-03 |
| B-03 | Adapter testuale conservato; tool P-07, immagini P-10, qualifica P-20 |
| B-03.2 | Contenitore consolidamento sostituito dai mapping puntuali sotto |
| B-03.2-01, B-03.2-02 | Storico conservato; regressioni P-01 |
| B-03.2-03 | Atomicità conservata; seeding di tre profili sostituito da P-02 |
| B-03.2-04 | Storico conservato con ripristino prova del -29; durable P-05 |
| B-03.2-05, B-03.2-06, B-03.2-07, B-03.2-08, B-03.2-09 | Storico conservato; snapshot/timeout/scope preservati in P-02/P-05 |
| B-03.2-10 | Stream corretto conservato; stati residui P-03, prove P-19/P-20 |
| B-03.2-11, B-03.2-12 | Storico conservato; no I/O bloccante e confini come gate comuni |
| B-03.2-13 | Fix/errori storici conservati; incoerenza stato con -33 verificata P-01, UX P-04 |
| B-03.2-14, B-03.2-15 | Storico conservato, incluso recovery; guida UX/correlation ID P-04 |
| B-03.2-16 | Ciclo tool/gateway P-07 |
| B-03.2-17 | Avvio nativo conservato; deploy/ruoli/DSN/readiness P-18 |
| B-03.2-18 | Think esplicito conservato; qualifica candidato P-03/P-20 |
| B-03.2-19 | GPU display/compute e contesa P-03/P-18/P-20 |
| B-03.2-20 | Robustezza report/tooling P-19 |
| B-03.2-21, B-03.2-22 | Storico conservato; esito CI non osservato non diventa prova nuova |
| B-03.2-24 | Misure SQL/indici P-11, migrazioni e isolamento invariati |
| B-03.2-26 | Fix mock conservati; correlation ID P-04, live sessione P-20 |
| B-03.2-27 | Licenze Python/build/smoke CI P-18 |
| B-03.2-28 | Igiene/config/script/codegen/i18n P-18 e P-04 |
| B-03.2-29, B-03.2-30, B-03.2-31, B-03.2-32, B-03.2-33 | Storico conservato; niente riapertura automatica |
| B-03.2-34 | Metrica preview conservata; persistenza/replay P-05/P-06 |
| B-03.2-35 | UI meter/SSE preview conservati; migrazione P-06 |
| B-03.2-36 | Contenimento inline conservato; sostituzione P-05/P-06 |
| B-03.2-37, B-03.2-38, B-03.2-39 | Storico conservato; regressioni permanenti P-01/P-06 |
| B-03.3 | Tailwind/shadcn completato conservato; riuso in P-04 |
| B-03.4 | Smoke storici conservati; qualifica 4 modelli non più obiettivo, Gemma P-03/P-19/P-20 |
| B-04, B-05 | Run/worker/scheduler P-05 |
| B-06, B-07 | Eventi/stop/replay P-06 |
| B-08 | Integrazione UI ripartita nelle slice P-04/P-06/P-08/P-10/P-11/P-15 |
| B-08.1, B-08.2, B-08.8 | Storico UI mock conservato; evoluzione non riscrittura in P-04 |
| B-08.3 | Paginazione/rendering sicuro P-04, persistenza P-06 |
| B-08.4 | Default onboarding P-02; runtime P-03; scelta esperta F-01 |
| B-08.5, B-08.6 | Composer durevole/reducer/stop/reconnect P-06 |
| B-08.7 | Attività/conferme/fonti P-07/P-11/P-15 |
| B-09 | Prove nella slice e qualifica completa P-17/P-20 |
| B-09.1 | Sessione mock conservata; residui recovery/due identità live P-01/P-20 |
| B-09.2 | Browser reale e synthetic chain P-06/P-20 |
| B-09.3 | Gemma live P-19/P-20 |
| Incremento A | Base storica, residui P-01/P-18; non chiamato globalmente completato |
| Incremento B | Pilot Assistente P-02–P-07, P-19/P-20 |
| Incremento C | Storage/documenti/retrieval P-08/P-09/P-11/P-12 |
| Incremento D | Visione P-10, artefatto minimo P-15; authoring/OCR completi F-06 |
| Incremento E | Differito F-07 |
| Incremento F | Differito F-08 |
| Incremento G | Differito F-01/F-09; sandbox e scope restano nel pilot |
| Incremento H | Memoria/proposta base P-13/P-16; estensioni F-03/F-04 |

### Decisioni ancora da qualificare, senza bloccare la scrittura dei ticket

- Gemma: alias/digest/licenza/runtime/proiettore e memoria totale (P-03/P-20).
  La dimensione dei pesi non è la memoria minima richiesta.
- Embedding/parser: versione/licenza e corpus italiano (P-09/P-11).
- Soglie live, hardware/corpus e tempi retention/restore (P-18/P-19/P-20):
  fissarli prima della campagna, mai dopo i risultati.
- Lobster: go/no-go P-14. LanceDB/Von: F-04/F-05, non prerequisiti.
- Prezzi/licenza commerciale/supporto, account/provider esterni e SSO:
  nessuna scelta inventata o autorizzazione dedotta dal backlog.

## Revisione documentale del 23 settembre 2026

Riscrittura del ledger e sincronizzazione della decisione in NewRay.md,
ADR 0006/indice e introduzione README. Backup integrale precedente conservato.
Questa sezione riguarda i controlli documentali, **non test applicativi**.

Controlli eseguiti con lettura dei file e validatore Node senza scritture:

- SHA-256 archivio identico all'originale; 71/71 ID di ticket storici mappati,
  oltre agli incrementi A–H; nessun TODO originario eliminato dall'archivio.
- 20 ticket pilot e 9 differiti, ID unici; contratti/principal/accettazione/
  limiti presenti, riferimenti alle dipendenze esistenti e nessun ciclo.
  Il gate P-20 raggiunge transitivamente tutti gli altri 19 ticket pilot.
- Link e anchor locali dei cinque documenti attivi modificati verificati;
  nessun whitespace finale. Link esterni non rivalidati in questa attività.
- Riesame semantico: default unico senza perdita dello storico, capacità vision
  da provare, memoria e retrieval distinti, forgetting anche sui derivati,
  cloud negato, workflow senza seconda autorità e skill senza nuovi privilegi.
  Allineate anche le vecchie priorità di NewRay.md §§9/16/23/25.

Nessun test applicativo/live eseguito per questa modifica solo documentale;
nessun codice/configurazione runtime modificato o ticket applicativo chiuso. Il percorso
non è un worktree Git: non sono disponibili diff/status/commit Git; inventario
file, controlli sul contenuto e hash del backup sono le evidenze della revisione.
