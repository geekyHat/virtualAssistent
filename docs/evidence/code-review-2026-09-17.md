# Revisione NewRay — codice, direzione e TODO ingegneristici

Data: 17 settembre 2026. Richiesta: analizzare il progetto rispetto al backlog,
individuare bug e migliorie sul lavoro esistente e indicare da dove riprendere.
Questo rapporto è un'evidenza e una specifica dei TODO: **gli stati delle
attività rimangono esclusivamente in [docs/backlog.md](../backlog.md)**.
Non sono state implementate correzioni al prodotto durante questa revisione.

## Valutazione

La direzione è coerente e la struttura è adatta al prodotto: un assistente di
lavoro locale, accessibile soltanto dalla WebUI, con profili manuali e un solo
modello generativo per run. Le priorità successive sono documenti/RAG,
produzione di elaborati, ricerca web, email e calendario. Il monolite modulare,
PostgreSQL con scope/RLS, le porte interne e i contratti generati consentono di
crescere senza introdurre un coordinatore LLM o un secondo backend.

Il codice attuale è una fondazione tecnica, **non ancora un assistente usabile
con continuità**. La WebUI gestisce l'identità; conversazioni, messaggi, profili
e catalogo hanno API. L'adapter genera testo attraverso Ollama, ma non è ancora
collegato a un worker/run della chat. Mancano, come pianificato, coda, eventi
persistenti, SSE, stop e recupero del run e pagina conversazioni. Non considero
RAG, posta e le altre funzioni C–H bug dell'incremento corrente.

Il problema principale è la distanza fra controlli nominali verdi e garanzie
di robustezza: guasti intermedi, concorrenza, terminali del protocollo,
immutabilità e rientro nella sessione richiedono consolidamento prima di B-04.
La revisione precedente aveva già individuato gran parte di questo lavoro;
conviene proseguire B-03.2, senza riscrivere l'architettura.

## Base esaminata e limiti

Letti NewRay.md, ADR 0001–0005, backlog, istruzioni locali, precedente revisione,
evidenze dei modelli locali; esaminati moduli identity/conversations/profiles/models,
route/DTO, bootstrap, migrazioni, fake/test, frontend sessione, CI, deployment e
script di import/qualifica. La migrazione Tailwind/shadcn è una decisione già
adottata nell'ADR 0005, ancora da realizzare; il CSS attuale non è di per sé un bug.

La cartella non è un repository Git riconosciuto (`git status --short` restituisce
`not a git repository`): non è disponibile un commit cui attribuire l'analisi.
Durante il lavoro sono comparse **modifiche concorrenti di identity** per
B-03.2-02. Non sono state sovrascritte. Le sonde usano una copia dei sorgenti in
`/tmp/newray-review-20260917`, identificata dal
[manifest SHA-256](code-review-2026-09-17.sha256). La copia locale non è un
artefatto di distribuzione; il manifest identifica i file letti, non qualifica
ogni file con un test. Identity era in transizione in quella copia: il bootstrap
atomico nuovo è esplicitamente escluso dalla qualifica di queste sonde.

Le conclusioni distinguono prove offline riprodotte, lettura statica e prove
live pregresse. Nessuna nuova inference GPU, importazione dei pesi, connessione
esterna di account o modifica di database operativo è stata eseguita. Non sono
state ripetute le prove PostgreSQL: mancano DSN test nell'ambiente della suite e
la fixture descritta sotto va corretta prima di rieseguirla su un cluster condiviso.
Nessun E2E browser o avvio Docker completo è stato qualificato qui.

## Confronto con il backlog

| Attività | Riscontro sul codice |
| --- | --- |
| A-01–A-07 | Stack, API/UI identità, tre migrazioni, codegen e gate esistono. Le chiusure storiche non coprono i difetti di sessione, transazioni, origine e deploy emersi dopo. A può correttamente restare in corso fino al consolidamento. |
| B-01 | CRUD e scope presenti; sequenze concorrenti, idempotenza e aggiornamento dell'ordinamento restano da consolidare. |
| B-02 | Profili/binding presenti; seeding, scelta della versione e snapshot non soddisfano ancora tutte le garanzie dichiarate. |
| B-03 | Adapter presente; terminali/guasti e timeout hanno difetti riprodotti. La porta è per ora testuale. |
| B-03.2-01 | Precedenza del DSN corretta, ma una nuova fixture migra esplicitamente `newray`: occorre riaprire la verifica di sicurezza dei test. |
| B-03.2-02 | Modifiche concorrenti introducono `OwnerBootstrap` e adapter transazionale. Verificare il risultato definitivo e le prove; non applicare alla cieca la correzione descritta nel vecchio R02. |
| B-03.2-08 | Il nome hardcoded è già stato rimosso dal provisioning operativo: `NEWRAY_DEFAULT_MODEL_NAME` passa da settings a wiring e servizio. Restano identità/digest, versionamento e binding preesistenti. |
| B-03.3 | Tailwind/shadcn pianificati; UI ancora CSS Modules. Coordinare con errori e recupero sessione. |
| B-03.4 | Script e quattro voci di catalogo presenti; evidenza live precedente parziale su `base`, con sonde fallite. «1 su 4 misurato» non equivale a «1 su 4 qualificato». |
| B-04–B-09 | Correttamente da fare; i moduli run/jobs e il flusso chat browser non sono ancora implementati. |
| C–H | Direzione documentata; non esiste implementazione da qualificare. Memoria esplicita B/C e contratti minimi di estensione andranno collocati in ticket concreti quando si pianifica C. |

## Riscontri prioritari

P1 = blocco al consolidamento/uso affidabile; P2 = correzione importante prima
del componente che ne dipende. Nessun P0 è dimostrato in questa revisione.

| Priorità / ticket | Evidenza attuale | Conseguenza |
| --- | --- | --- |
| P1 / B-03.2-01 | `operational_database` costruisce il DSN `/newray` e chiama `command.upgrade(head)`. Sonda con Alembic intercettato: bersaglio `newray`. | La suite può modificare il DB operativo prima di misurare che il downgrade lo lasci invariato. La precedenza DSN corretta non elimina questo effetto esplicito. |
| P1 / B-03.2-03 | Dopo un guasto alla seconda versione, il retry lascia 2 profili, 1 versione e 1 profilo visibile nel fake. Adapter con commit separati conferma la causa strutturale. | Provisioning incompleto che il controllo «lista non vuota» non ripara. Concorrenza PostgreSQL da provare. |
| P1 / B-03.2-04 | `MAX(sequence)+1` senza serializzazione; `UNIQUE` impedisce duplicati ma può far fallire uno dei writer. Append non aggiorna la conversazione. Lettura statica. | Possibili errori sui messaggi concorrenti e ordinamento delle conversazioni non aggiornato. |
| P1 / B-03.2-05 | Le request extensions mostrano connect/read/write/pool tutti `null` con timeout omesso; `ChatRequest` accetta -1 secondi. | Una chiamata può non avere più alcun timeout; manca comunque una deadline complessiva. |
| P1 / B-03.2-06 | Settings accetta `0.0.0.0` con solo cookie Secure; revoca con Origin su altra porta restituisce 204. | I cookie non configurano TLS; manca la validazione server-side dell'origine. La sonda ASGI non è una prova di exploit browser. |
| P2 / B-03.2-07 | SQL ordina versioni crescente; `get_profile` prende la prima, `list_profiles` calcola il massimo. | La risoluzione può usare una versione più vecchia di quella visualizzata. Lettura statica, non nuova prova PostgreSQL. |
| P2 / B-03.2-09 | Scrivere `snapshot.parameters['temperature']=999` riesce e modifica il binding condiviso nel fake. | Lo snapshot non è profondamente immutabile; il digest può inoltre essere assente. |
| P1 / B-03.2-10 | `done_reason=length` diventa `stop`; EOF senza done e oggetto sconosciuto terminano senza errore; due terminali producono due Completion; disconnessione diventa `NetworkUnreachable`. | Risposta troncata/incompleta non distinguibile da esito normale, o errore infrastrutturale fuori dal contratto. |
| P2 / B-03.2-11 | Repository sincroni chiamati dentro route async; catalogo letto per ogni profilo e due volte nella risoluzione. | Attese DB bloccano il ciclo eventi e le letture del catalogo sono ridondanti/incoerenti nel tempo. |
| P2 / B-03.2-12 | Il checker restituisce zero violazioni per core→proprio adapter e core→infrastructure. | CI verde non garantisce tutti i confini che dichiara. |
| P2 / B-03.2-13 | Pagina collega solo CONFLICT del bootstrap, non altri errori di POST né pending/errori della revoca. | Errori non visibili; recupero e prevenzione doppi invii incompleti. Il pending del bootstrap è già collegato e va conservato. |
| P1 / B-03.2-14 | Route solo bootstrap/me/revoke; nessun login/recovery. Revoca→me riprodotto come 204→401. | Dopo logout, cookie perso o scadenza manca un normale rientro agli stessi dati. |
| P2 / B-03.2-15 | Titolo vuoto produce 422 con sola chiave `detail`; il client si aspetta l'envelope applicativo. | Input errato presentato come guasto interno; tutti i 500 sono attualmente retryable. |
| P1 / B-03.2-18 | Adapter ignora `message.thinking` e non invia `think`; evidenza live precedente registra attesa e risposta vuota. | Consumo di tempo/token senza contenuto visibile e completamento fuorviante. Nessuna nuova misura GPU qui. |
| P2 / B-03.2-20, nuovo | Warmup sintetico fallito: `OSError` esce da `qualify`, client non chiuso. `main_async` scrive solo dopo il ritorno di `qualify`. | Il modello fallito può non comparire nel JSON; un rapporto precedente può restare sul disco e sembrare quello corrente. |
| P2 / B-03.2-21, nuovo | CI esegue pytest in backend; test degli script sono altrove. `scripts/tests/test_local_models.py` chiama `unittest.main()` all'import. | Gli strumenti operativi nuovi non partecipano ai gate ordinari; integrarli con discovery non è ancora sicuro. |

Il campo terminale e l'opzione thinking sono confermati dalla
[documentazione primaria Ollama](https://docs.ollama.com/api/chat).
HTTPX documenta che `timeout=None` disabilita i timeout e distingue il timeout
di inattività dalla durata complessiva: [documentazione HTTPX](https://www.python-httpx.org/advanced/timeouts/).
Sono verifiche di protocollo; non qualificano il comportamento dei GGUF locali.

## TODO ingegneristici sul lavoro esistente

Gli ID riusano il backlog. Le caselle descrivono il lavoro richiesto dalla
revisione, non un secondo registro di stato. Per ogni chiusura servono change
identificabile, prova negativa, prova nominale e aggiornamento del solo ledger.

### B-03.2-01 — Isolare davvero tutte le prove di migrazione (P1)

Proprietario: tooling DB/migrazioni. File: [fixture integration](../../backend/tests/integration/conftest.py),
[test migrazioni](../../backend/tests/integration/test_migrations.py),
[risoluzione DSN](../../backend/migrations/urls.py). Principal: ruoli tecnici test,
mai credenziali del servizio applicativo; effetti: DDL solo su database sacrificabili.

- [ ] Sostituire `operational_database` con un secondo DB sentinella temporaneo,
  creato dalla fixture. Non fare upgrade di alcun database dal nome fisso `newray`.
- [ ] Puntare la variabile «operativa» della prova alla sentinella; verificare
  prima/dopo revisione Alembic **e dati sentinella**, non soltanto nomi delle tabelle.
- [ ] Preservare la precedenza del DSN esplicito già corretta. Verificare nome,
  istanza e ownership del bersaglio prima di ogni operazione distruttiva;
  usare il parser URL SQLAlchemy invece della concatenazione di stringhe dei DSN.
- [ ] Portare il teardown in un `try/finally` che copra anche errori di setup;
  chiudere gli engine temporanei e limitare il DROP ai DB creati dalla fixture.
- [ ] Accettazione: la sonda offline non osserva mai `/newray`; su PostgreSQL
  due DB temporanei, upgrade/downgrade sul solo target, sentinella intatta;
  fallimento di setup senza residui. Solo allora richiudere il ticket.

### B-03.2-02 — Completare la correzione transazionale di identity (P1)

Proprietario: identity; porte `OwnerBootstrap`/repository, adapter e wiring.
Principal: bootstrap locale monouso; dati: organizzazione, owner, sessione.

- [ ] Integrare e revisionare la modifica concorrente `OwnerBootstrap` già
  iniziata, evitando una seconda implementazione della stessa transazione.
- [ ] Verificare una sola transazione per i tre record; conflitto tradotto
  dopo rollback, nessuna sessione perduta o organizzazione orfana.
- [ ] Aggiornare tutti i consumer, fake e generatori di contratti alla firma
  definitiva; mantenere separati bootstrap e futuro login.
- [ ] Accettazione: guasto dopo ogni scrittura e due bootstrap simultanei su
  PostgreSQL producono zero stato parziale, un solo vincente, un conflitto
  gestito e un retry utile. Rileggere le evidenze concorrenti prima di chiudere.

### B-03.2-03 — Provisioning atomico dei profili (P1)

Proprietario: profiles; [servizio](../../backend/src/newray/modules/profiles/application.py),
[adapter](../../backend/src/newray/modules/profiles/adapters/postgres.py), porte e migrazione.
Principal: proprietario autenticato; dati: binding, tre profili e relative versioni.

- [ ] Sostituire la sequenza di commit indipendenti con una porta transazionale
  stretta di provisioning, con scope esplicito; nessuna rete dentro la transazione.
- [ ] Definire vincolo/lock scoped che renda idempotente la prima lettura
  concorrente. «Esiste almeno un profilo» non è un criterio di completezza.
- [ ] Definire riparazione/migrazione dei record parziali già creati senza
  riscrivere versioni esistenti; distinguere profilo senza versione da assenza.
- [ ] Accettazione: fault injection a ogni passo e due GET concorrenti reali;
  esattamente tre profili completi, binding senza duplicati inutili, nessun 500;
  principal B non vede record di A. Test fake e PostgreSQL separati.

### B-03.2-04 — Scritture conversazioni affidabili (P1)

Proprietario: conversations; [adapter](../../backend/src/newray/modules/conversations/adapters/postgres.py),
servizio/porte/DTO e migrazione. Principal: proprietario della conversazione;
effetti: append, rename, delete e relativa ricevuta idempotente.

- [ ] Serializzare la sequenza per conversazione con lock della riga padre
  o contatore atomico; append e `updated_at` nella stessa transazione corta.
- [ ] Introdurre idempotency key scoped per principal/operazione e hash del
  payload; stesso input restituisce lo stesso esito, input diverso è conflitto.
- [ ] Aggiungere versione attesa per rinomina/cancellazione; definire il
  comportamento se delete concorre con append. Non riprovare ciecamente una mutazione.
- [ ] Migrare esplicitamente schema e contratti; verificare come la paginazione
  su `updated_at` si comporta mentre cambiano le conversazioni, senza promettere
  snapshot stabili che la query non offre.
- [ ] Accettazione: due writer PostgreSQL riescono con sequenze distinte;
  retry non duplica; scrittura obsoleta è conflitto; attività recente riordina
  correttamente; isolamento, cascata e ricevute restano scoped.

### B-03.2-05 — Timeout effettivi e budget di durata (P1)

Proprietario: infrastructure/network e models; [client](../../backend/src/newray/infrastructure/network.py),
[ChatRequest](../../backend/src/newray/modules/models/domain.py). La futura deadline
del run appartiene a runs/jobs; nessun nuovo modulo vuoto in questa correzione.

- [ ] Quando manca l'override, omettere il parametro HTTPX e conservare il
  timeout del client; distinguere esplicitamente «eredita» da «disabilita».
- [ ] Validare valori positivi; separare connect/read/write/pool dalla deadline
  complessiva. Non chiamare un read timeout «durata massima del run».
- [ ] Definire nel contratto la cancellazione e la deadline che B-04/B-05
  applicheranno anche a uno stream che continua a produrre piccoli chunk.
- [ ] Accettazione: request extensions con/senza override, server lento,
  stream interminabile controllato e cancellazione; connessione chiusa su ogni uscita.

### B-03.2-06 — Confine HTTP personale e origine (P1)

Proprietario: bootstrap/interfaces/deploy; settings, main, middleware e proxy Vite.
Principal: anche bootstrap anonimo; effetti: tutte le mutazioni con cookie.

- [ ] Nella modalità personale rifiutare bind non-loopback indipendentemente
  dal flag Secure. Una futura modalità ufficio deve avere TLS/proxy e autenticazione
  configurati e verificati, non essere abilitata dal solo attributo del cookie.
- [ ] Definire origine canonica e host ammessi; validare le mutazioni, inclusi
  bootstrap e revoca. Stabilire esplicitamente il trattamento di Origin assente/null.
- [ ] Coprire proxy Vite same-origin e non fidarsi di header proxy arbitrari.
- [ ] Accettazione: origine/host estranei rifiutati prima di effetti, stesso
  origin consentito, test browser reale del flusso cookie/CSRF e avvio remoto negato.

### B-03.2-07 — Una sola versione corrente (P2)

Proprietario: profiles/adapters; contratto `ProfileRepository`, scope del principal.

- [ ] Selezionare in SQL il massimo per `(created_at, id)` per profilo, usando
  la stessa query/regola in lista e lettura singola; evitare di caricare tutte le versioni.
- [ ] Conservare ordinamento stabile dei profili e allineare il fake.
- [ ] Accettazione: due versioni con binding diversi, anche con stesso timestamp;
  lista/get/resolve concordano su PostgreSQL e non leggono versioni di altri utenti.

### B-03.2-08 — Completare il binding esplicito già configurabile (P2)

Proprietario: profiles/models/bootstrap. File: settings, wiring, servizio profiles,
catalogo locale; principal: proprietario dei nuovi profili. Non cambiare modello
in base al testo della richiesta.

- [ ] Conservare `NEWRAY_DEFAULT_MODEL_NAME`: il vecchio hardcoding operativo
  è già rimosso. Non duplicare un altro default nei servizi o negli script.
- [ ] Risolvere nome canonico/digest con regola esplicita; modello assente o
  config assente danno un errore recuperabile, mai una sostituzione automatica.
- [ ] Stabilire nuova versione/binding per profili già provisionati: il cambio
  di variabile non li aggiorna oggi e non deve riscriverli silenziosamente.
- [ ] Accettazione: configurazione locale scelta crea binding corretto;
  riavvio non altera quelli esistenti; aggiornamento esplicito crea una versione;
  digest e capacità restano distinti fra dichiarati e qualificati.

### B-03.2-09 — Snapshot realmente immutabile (P2, prima di B-04)

Proprietario: profiles/models; `ResolvedBinding`, DTO e successivo contratto run.
Principal: proprietario del run; dati: versione, binding, modello e parametri.

- [ ] Usare rappresentazione profondamente immutabile e serializzabile, con
  copia dei valori in ingresso; validare anche liste/dizionari annidati e numeri non finiti.
- [ ] Includere ID stabili della versione e binding, digest obbligatorio per
  esecuzione e istruzioni/versioni necessarie al futuro snapshot del run.
- [ ] Definire verifica del digest prima dell'esecuzione se il tag può cambiare
  mentre il lavoro è in coda; in caso di divergenza fallire in modo esplicito.
- [ ] Accettazione: mutazioni di sorgente/snapshot non cambiano il run;
  round-trip JSON/DB conserva il significato; un tag sostituito non esegue altri pesi.

### B-03.2-10 — Protocollo Ollama e terminale unico (P1)

Proprietario: models/adapters; [ollama.py](../../backend/src/newray/modules/models/adapters/ollama.py),
porta `ChatModel` e contract test. Effetto: generazione locale; nessun tool eseguito.

- [ ] Leggere `done_reason`; sostituire le fixture che oggi inviano
  `finish_reason` e quindi confermano l'assunzione sbagliata dell'adapter.
- [ ] Validare lo schema e implementare una sequenza di eventi con un solo
  terminale. EOF senza done, terminali duplicati e chunk dopo il terminale devono
  avere comportamento documentato; non produrre due Completion.
- [ ] Mappare disconnessioni anche durante il body, mantenendo separati indisponibilità
  iniziale, guasto della generazione e timeout; niente eccezioni network oltre la porta.
- [ ] Prescrivere `aclosing`/`aclose` al consumer: uscire da `async for` con
  `break` non è una garanzia sufficiente di chiusura tempestiva del generatore.
- [ ] Accettazione: casi riprodotti nella tabella più cancel e contenuto terminale;
  successivo smoke sul digest scelto, con runtime e limiti registrati.

### B-03.2-11 — I/O asincrono e lifecycle (P2)

Proprietario: profiles/bootstrap/infrastructure; repository e composition root.
Principal: scope conservato anche passando a thread o driver async.

- [ ] Scegliere adapter async oppure offload del blocco sincrono completo;
  non dividere una transazione fra thread e non condividere Connection tra task.
- [ ] Leggere il catalogo una volta per operazione e riusare lo stesso risultato.
- [ ] Possedere gli engine nel bootstrap e chiuderli nel lifespan, incluso
  shutdown per errore; configurare il pool senza moltiplicarlo inconsapevolmente.
- [ ] Accettazione: DB artificialmente lento non blocca richiesta indipendente;
  numero chiamate catalogo costante; scope non residua nel pool e risorse chiuse.

### B-03.2-12 — Checker architetturale aderente ai vincoli (P2)

Proprietario: tooling; [check_architecture.py](../../scripts/check_architecture.py).
Nessun principal o dato privato: legge sorgenti di progetto.

- [ ] Controllare core→adapter anche nello stesso modulo e core→infrastructure.
- [ ] Risolvere import relativi, `from . import ...`, package/re-export e
  archi del grafo verso i file effettivi, preservando gli import pubblici consentiti.
- [ ] Aggiungere negativi permanenti su piccoli alberi sintetici, non solo
  esecuzioni manuali dichiarate nello storico; testare anche almeno un ciclo.
- [ ] Accettazione: tutti i divieti falliscono in CI e il codice conforme passa.

### B-03.2-13 — Errori e stato delle mutation nella UI (P2)

Proprietario: web/session; [IdentityPage](../../web/src/pages/IdentityPage.tsx),
hook e pannello, client API e i18n. Principal: sessione corrente.

- [ ] Separare query identità, bootstrap e revoca; passare errori/pending
  pertinenti al pannello. Il bootstrap ha già il bottone disabilitato durante pending.
- [ ] Evitare doppi invii anche nel submit handler e bloccare revoche ripetute;
  mostrare recupero riferito alla POST fallita, non soltanto ripetere GET /me.
- [ ] Conservare l'identità finché non è confermata la revoca; errori di rete
  visibili, testo it/en, focus e annunci accessibili.
- [ ] Accettazione: POST 500, rete interrotta, revoca fallita e doppio submit;
  test d'interazione e browser/tastiera, coordinati con B-03.3.

### B-03.2-14 — Rientro autenticato e pulizia sessione (P1 prodotto)

Proprietario: identity e web/session. Contratti login/recovery distinti dal
bootstrap; dati: credenziali/sessioni e cache private. Richiede una piccola
decisione di prodotto sul recupero locale, non una password condivisa.

- [ ] Definire e implementare accesso successivo all'owner esistente con
  librerie mantenute; scadenza, revoca e recupero locale non ricreano l'owner.
- [ ] Introdurre il minimo schema necessario con migrazione, rotazione e
  protezione dagli abusi; non esporre credenziali in DTO, log o storage browser.
- [ ] Cancellare query private e richieste pendenti al logout/cambio identità;
  chiavi dei dati private scoped. La sola invalidazione di `['identity']` non basta
  per la futura chat. Non esistono ancora stream da chiudere: aggiungere l'hook con B-06.
- [ ] Accettazione: bootstrap→logout→login restituisce gli stessi dati;
  cookie perso/scaduto recuperabile, identità errata negata, nessun reset DB.

### B-03.2-15 — Errori pubblici uniformi (P2)

Proprietario: interfaces/http e web/shared/api; error handler, DTO/codegen.
Principal: ogni richiesta; log minimizzati, senza payload privati o DSN.

- [ ] Gestire validazione e altri errori HTTP con envelope dichiarato nei
  contratti; campi invalidi descritti senza riemettere dati sensibili in ingresso.
- [ ] Validare lunghezza/formato del correlation ID e collegarlo ai log.
- [ ] Derivare retryable dall'operazione: un 500 successivo a una scrittura
  non prova che ripetere sia sicuro. Coordinare con ricevute/idempotenza del ticket 04.
- [ ] Accettazione: 400/401/404/409/422/500 coerenti, client localizzabile,
  niente stack/segreti, codegen e controllo drift verdi.

### B-03.2-16 — Contratto minimo tool, senza anticipare moduli speculativi (P2)

Proprietario: models e futuro gateway tools/access; principal del run sempre
esplicito. Oggi `ChatRole` e `StreamEvent` coprono soltanto testo.

- [ ] Definire tool call/result, ID, schema e capability nella porta;
  continuazione sul modello dello snapshot, nessun router o fallback.
- [ ] Realizzare una slice minima con tool sintetico/locale senza effetti
  esterni, policy server-side e ricevuta reale. Aggiungere porte con quel consumer.
- [ ] Documentare come revoca e approvazione saranno rivalutate prima
  dell'effetto; una prova runtime di tool calling non qualifica il gateway dell'app.
- [ ] Accettazione: tool sconosciuto negato, schema invalido respinto,
  nessuna azione dedotta dal testo, stesso modello nei due turni; aggiornare
  B-04/B-05 con dipendenze concrete prima di introdurre effetti.

### B-03.2-17 — Deployment e documentazione veritieri (P2)

Proprietario: deploy/bootstrap/docs. Principal: separare admin DB,
migratore e applicazione. File: Compose, Dockerfile, entrypoint e README/AGENTS.

- [ ] Separare superuser di inizializzazione e ruolo migratore non superuser;
  verificare che il processo app usi un ruolo non owner e senza BYPASSRLS.
- [ ] Rimuovere la credenziale migrazioni dall'ambiente prima di avviare
  l'API: `run_local_api.sh` lo fa, `deploy/entrypoint.sh` attualmente no.
  Preferire un job migrazioni distinto quando si qualifica Compose.
- [ ] Bloccare immagini/runtime identificabili e provare build, avvio,
  migrazione da revisione precedente e shutdown su dati sintetici.
- [ ] Esplicitare configurazione Ollama/modello e percorso di serving WebUI
  same-origin: Compose attuale comprende DB/API, non una distribuzione completa della chat.
- [ ] Allineare `backend/AGENTS.md` (dice ancora che non esistono package/test),
  README/script e frasi «Ollama assente» del backlog distinguendo stato e storico.
- [ ] Accettazione: deployment reale, RLS con due principal nel deployment,
  processo app senza segreto migratore, asset locali raggiungibili quando inclusi;
  `docker compose config` da solo non chiude il ticket.

### B-03.2-18 — Thinking, contenuto e uso dei token (P1)

Proprietario: models, poi runs/eventi/UI. Dati: contenuto finale, stato di
elaborazione e metriche; nessuna esposizione del ragionamento interno implicita.

- [ ] Decidere un contratto esplicito: disattivare thinking dove supportato
  oppure gestire la fase separatamente. Preferire uno stato di avanzamento
  comprensibile; mostrare il ragionamento grezzo non è necessario per segnalare attività.
- [ ] Non presentare contenuto vuoto/troncato come risposta completata normale;
  trasmettere motivo terminale e limiti al run.
- [ ] Registrare token totali effettivi; separare sottoconteggi soltanto se
  il runtime li fornisce, altrimenti marcarli non disponibili, senza inventare misure.
- [ ] Accettazione: fixture con thinking, budget esaurito e content vuoto;
  prova live del modello esatto con limite temporale dichiarato e stato visibile.

### B-03.2-19 — Isolamento della GPU e precondizioni (P2)

Proprietario: script operativi/deploy, futuro scheduler. Effetto: solo runtime
locale autorizzato; non fermare servizi dell'utente per liberare VRAM implicitamente.

- [ ] Generalizzare il launcher oltre il primo risultato del glob
  `/usr/lib/ollama/cuda_v*`: oggi nasconde una sola directory, non tutti i backend presenti.
- [ ] Legare la verifica VRAM all'identità della GPU selezionata, non al
  primo device con contatori. In modalità di isolamento richiesto, contatori o
  confine non verificabili devono impedire l'avvio della qualifica.
- [ ] Verificare anche selezione CPU e backend alternativi con evidenze;
  non estendere per deduzione la prova fatta su questa macchina.
- [ ] Accettazione: due directory backend sintetiche nel test launcher,
  GPU piena/contatori assenti, prova operativa senza discovery sulla GPU display;
  documentare contesa con servizi esterni e portare il confine nel deployment pertinente.

### B-03.2-20 — Rapporto di qualifica durevole anche sui fallimenti (P2, nuovo)

Proprietario: [qualify_local_models.py](../../scripts/qualify_local_models.py).
Principal: operatore locale, corpus sintetico; effetti: chiamate al runtime,
caricamento/scaricamento, rapporto JSON. Dipendenza: B-03.4.

- [ ] Creare un record di esecuzione all'inizio; raccogliere errori di discovery,
  warmup, sonde e unload nello stesso record. Distinguere fallimento, interruzione,
  precondizione non soddisfatta e caso non provato.
- [ ] Persistire progressivamente e con sostituzione atomica il rapporto;
  usare un ID univoco o stato started/completed, senza lasciare indistinguibile
  un JSON vecchio dopo il fallimento del primo modello.
- [ ] Chiudere sempre `HttpClient`; conservare l'errore iniziale anche se
  unload fallisce. Fermarsi senza caricare altri modelli se il rilascio non è confermato.
- [ ] Registrare hash di script/adapter/corpus, digest completo, GPU scelta,
  runtime, contesto e opzioni. Il report attuale non identifica il codice e l'hardware
  in modo sufficiente per una qualifica ripetibile.
- [ ] Accettazione: fault injection offline in ogni fase produce JSON valido,
  exit non zero, client chiuso e nessun falso successo; poi rieseguire `base`
  e gli altri tre modelli solo con risorse disponibili e confine GPU verificato.

### B-03.2-21 — Portare gli script operativi nei gate ordinari (P2, nuovo)

Proprietario: tooling/CI; scripts/tests, workflow e README. Nessuna GPU o rete
pubblica nei test normali; non importare automaticamente pesi durante la collection.

- [ ] Rendere i moduli test importabili senza `unittest.main()` incondizionato;
  risolvere i path rispetto al file, non alla working directory.
- [ ] Aggiungere comando CI esplicito per test offline degli script e dei
  checker; estendere lint/format alle directory realmente mantenute.
- [ ] Aggiungere casi negativi pertinenti: catalogo/schema, path/symlink,
  precondizioni GPU, fallimento qualifica/report e isolamento launcher.
- [ ] Accettazione: un difetto introdotto in uno script fa fallire il job;
  stesso comando funziona dal percorso documentato, zero chiamate GPU/download;
  prove live restano opt-in e separate.

## Migliorie di pianificazione da preservare

- Un'unica fonte degli stati: riferire questo rapporto da B-03.2, senza creare
  un secondo backlog e senza chiudere ticket per la sola presenza di codice.
- Chiudere una chat testuale completa prima di espandere RAG e integrazioni;
  portare fin da quel flusso ownership, idempotenza, revoca e recupero.
- Conservare le prove buone: dominio leggero, principal distinto dal profilo,
  RLS/FK composite, codegen e test offline rapidi. Non servono microservizi,
  un framework multiagente o un altro database per risolvere questi difetti.
- Prima di dati reali definire retention e restore; prima di C collocare
  memoria esplicita e installer del primo componente necessario in vertical slice
  verificabili. Le sole descrizioni in NewRay.md non sono implementazioni.

## Verifiche realmente eseguite

I gate ordinari sono stati eseguiti prima delle modifiche concorrenti a identity
(prima esecuzione frontend alle 13:19 ora locale). Non attribuire quei risultati
al codice successivamente modificato senza una nuova esecuzione.

| Comando | Esito |
| --- | --- |
| `backend/: .venv/bin/python -m pytest -q` | 133 passed, 29 skipped: 27 integration PostgreSQL + 2 live; 2 warning di deprecazione TestClient/AnyIO. |
| `backend/: .venv/bin/ruff check .` | Passato. |
| `backend/: .venv/bin/ruff format --check .` | 89 file conformi. |
| `backend/: .venv/bin/mypy` | Passato, 56 sorgenti. |
| `web/: npm run check` | Format, lint, typecheck, 11 test, licenze di 336 pacchetti, build e budget passati; JS 302,8 KiB, CSS 2,3 KiB. |
| `python3 scripts/check_architecture.py` | Passato, 56 file; limiti del verificatore riprodotti separatamente. |
| `backend/.venv/bin/python scripts/generate_contracts.py --check` | Passato, nessun drift osservato in quella esecuzione. |
| `python3 scripts/tests/test_local_models.py` | 2 test passati, esecuzione separata dalla CI corrente. |
| Sonde del rapporto sulla copia congelata | Eseguite offline; risultati in [JSON](code-review-2026-09-17-probes.json). |

Riproduzione delle sonde sulla working copy disponibile:

```text
backend/.venv/bin/python docs/evidence/code-review-2026-09-17-probes.py
```

Il parametro facoltativo è il percorso di una copia del repository; per questa
analisi è stato usato `/tmp/newray-review-20260917`. Lo
[script](code-review-2026-09-17-probes.py) usa trasporto HTTP in memoria e
intercetta Alembic: non esegue migrazioni. È uno strumento diagnostico che stampa
osservazioni, **non** una suite che deve continuare a confermare i bug dopo la
correzione. L'esecuzione ASGI si bloccava nella sandbox; rieseguita fuori dalla
sandbox con autorizzazione è terminata in meno di un secondo, senza connessioni reali.

## Da dove continuare

L'ultimo filone sviluppato è **B-03.4**, collegamento e qualifica parziale dei
modelli locali; il consolidamento aveva chiuso B-03.2-01 e sta ricevendo lavoro
su B-03.2-02. Suggerisco di **riprendere da questo punto, completando prima
B-03.2**, non di iniziare B-04 ignorando i difetti già noti.

Ordine operativo: correggere la fixture residua di **B-03.2-01**; integrare e
verificare **B-03.2-02** in lavorazione; poi **03–04** (integrità dei dati),
**05/10/18** (inference), **07–09/11** (binding e accesso DB),
**06/13–15** (sessione e confine HTTP), **12/17/19–21** (gate/deploy/qualifica)
e il contratto minimo **16**. Riprendere **B-03.4** con queste correzioni e
risorse disponibili. B-03.3 va coordinato con il lavoro sessione e completato
prima di B-08.

Una volta soddisfatto il gate B-03.2, il successivo TODO funzionale è
**B-04 — run, stati, snapshot e recupero**, seguito da B-05–B-09. Non introdurre
nel frattempo fallback di modello, giudici sincroni o moduli senza consumer.
