# Revisione del codice — 16 settembre 2026

La base architetturale è buona, ma la robustezza delle prime funzionalità è
inferiore a quanto indicano alcune chiusure del backlog. Conviene consolidare
identità, persistenza e adapter prima di costruirvi run, worker e streaming UI.
Non emerge una ragione per riscrivere il progetto.

Analisi richiesta su qualità, efficacia e allineamento con NewRay.md e
docs/backlog.md. Nessuna correzione applicativa o modifica agli stati del
backlog è stata effettuata. Questo documento registra risultati e proposte;
non costituisce un secondo registro di avanzamento.

## Perimetro e verifiche

Esaminati dominio, servizi, adapter, API, migrazioni, bootstrap, WebUI sessione,
test, generazione dei contratti, controllo architetturale, CI e deployment.
La cartella non è un repository Git utilizzabile durante la revisione:
l'identificazione dei file esaminati è nel
[manifest SHA-256](code-review-2026-09-16.sha256).

| Verifica eseguita | Risultato |
| --- | --- |
| Backend: `.venv/bin/python -m pytest -q` | 119 passed, 29 skipped; 2 avvisi di deprecazione |
| Backend: Ruff lint e format | Superati; 87 file già formattati |
| Backend: mypy strict | Superato; 56 file sorgente |
| Web: `npm run check` | Superato; 11 test, formato, lint, tipi, controllo licenze, build e budget |
| `python3 scripts/check_architecture.py` | Successo sui 56 file attuali; limiti del controllo in R12 |
| `python3 scripts/generate_contracts.py --check` | Nessun drift |
| Probe aggiuntive con dati sintetici | Riprodotti i casi indicati sotto, senza account esterni o inference reale |

I 29 skip sono 27 test integration PostgreSQL e 2 test live Ollama: le relative
variabili non erano configurate. Non ho ripetuto le prove PostgreSQL dichiarate
nel backlog, né eseguito Docker, GitHub Actions o prove browser E2E. Il test
client FastAPI delle probe ha richiesto esecuzione fuori sandbox per un blocco
nella comunicazione fra thread. Le probe HTTP usano trasporto in memoria.

Il successo dei controlli esistenti è reale, ma non copre i difetti seguenti.
P1 indica una correzione prioritaria per sicurezza dei dati o affidabilità;
P2 indica un difetto concreto da correggere nel consolidamento della base.

## Problemi prioritari

### R01 — P1: i test Alembic possono migrare o svuotare il database operativo

File: [migrations/env.py](../../backend/migrations/env.py), righe 29–31;
[fixture integration](../../backend/tests/integration/conftest.py), funzione
`test_databases`; [test_migrations.py](../../backend/tests/integration/test_migrations.py),
funzione `test_downgrade_rimuove_le_tabelle`. Riguarda A-03/A-06.

La fixture imposta `sqlalchemy.url` sul database temporaneo. `env.py` lo
sovrascrive però con `NEWRAY_MIGRATION_DATABASE_URL`, se presente nella shell.
È proprio la variabile che le istruzioni di avvio chiedono di esportare.
Anche il test che esegue `downgrade base` passa da questo ambiente: può quindi
operare sul database di sviluppo/operativo anziché su quello temporaneo.

**Verifica:** configurazione Alembic impostata su `newray_it_review`, ambiente
su `newray_operational`; intercettando `engine_from_config`, il DSN effettivo
risulta quello operativo. Nessuna connessione o migrazione è stata eseguita.

**Correzione:** rendere esplicita e prioritaria la configurazione iniettata dai
test, isolare l'ambiente delle fixture e verificare il database bersaglio prima
di upgrade/downgrade. Un test di precedenza delle configurazioni deve coprire
questa combinazione senza connettersi a database reali.

### R02 — P1: il bootstrap non è transazionale e può lasciare l'installazione bloccata

File: [identity/application.py](../../backend/src/newray/modules/identity/application.py),
righe 61–64; [identity/adapters/postgres.py](../../backend/src/newray/modules/identity/adapters/postgres.py).
Riguarda A-02/A-03/A-04, NewRay.md §§7.4 e 20.3.

Organizzazione, proprietario e sessione sono salvati con transazioni distinte.
Un fallimento dopo il proprietario lascia l'owner persistito senza sessione;
il tentativo successivo riceve CONFLICT. Inoltre ogni bootstrap ripetuto crea
prima una nuova organizzazione, anche quando il proprietario esiste già.
Il vincolo di unicità protegge dall'owner duplicato, non rende atomico il caso d'uso.

**Verifica:** errore iniettato nel salvataggio della sessione; al retry il
servizio restituisce CONFLICT e il fake contiene due organizzazioni.
Gli adapter confermano i commit separati; non è una prova PostgreSQL live.

**Correzione:** una transazione per organizzazione+owner+sessione con unità di
lavoro o porta applicativa dedicata. Verificare rollback sui punti di guasto e
assenza di organizzazioni orfane quando il bootstrap è già avvenuto.

### R03 — P1: il seeding dei profili non recupera i guasti ed è vulnerabile alla concorrenza

File: [profiles/application.py](../../backend/src/newray/modules/profiles/application.py),
righe 80–117; [profiles/adapters/postgres.py](../../backend/src/newray/modules/profiles/adapters/postgres.py).
Riguarda B-02.

Binding, profili e versioni vengono inseriti con commit distinti. Se esiste
anche un solo profilo completo, `ensure_defaults` non fa più nulla. Un errore
dopo il primo profilo lascia quindi mancanti gli altri. Un errore dopo il solo
binding rende il retry incompatibile con il vincolo sul nome del binding.
Due inizializzazioni simultanee possono entrambe vedere lo scope vuoto e
collidere sugli INSERT: i vincoli UNIQUE non gestiscono da soli il recupero.

**Verifica:** guasto iniettato sull'inserimento del secondo profilo; dopo aver
rimosso il guasto e ripetuto il seeding resta un solo profilo. La collisione
fra transazioni è rilevata dal codice, non riprodotta su PostgreSQL in questa sessione.

**Correzione:** inizializzazione atomica e idempotente anche sotto concorrenza;
testare guasti intermedi e due chiamate simultanee sullo stesso scope.

### R04 — P1: MAX(sequence)+1 non garantisce append concorrenti riusciti

File: [conversations/adapters/postgres.py](../../backend/src/newray/modules/conversations/adapters/postgres.py),
metodo `PostgresMessageStore.append`, righe 153–179. Riguarda B-01.

Due INSERT concorrenti possono leggere lo stesso massimo e proporre la stessa
sequenza. UNIQUE impedisce di salvare il duplicato, ma uno dei messaggi fallisce
con IntegrityError; manca serializzazione o recupero. Basta inviare da due
schede, e il worker futuro aggiungerà un altro produttore di messaggi.

**Evidenza:** analisi della query e delle transazioni. La suite presente verifica
la sequenza in uso seriale, non questa interleaving; nessuna riproduzione DB live.

**Correzione:** serializzare l'assegnazione per conversazione, per esempio con
lock della riga padre e lettura della sequenza nella successiva istruzione,
oppure contatore atomico. Testare due writer reali: entrambi devono riuscire
con sequenze distinte. Considerare anche le chiavi di idempotenza previste dal contratto API.

### R05 — P1: omettere il timeout della chat disabilita tutti i timeout HTTP

File: [infrastructure/network.py](../../backend/src/newray/infrastructure/network.py),
righe 129–135. Riguarda B-03 e NewRay.md §§6.1/8.4.

Quando `ChatRequest.timeout` non è indicato, `post_stream` passa esplicitamente
`timeout=None` a HTTPX. Questo annulla i default del client, inclusi connessione,
lettura, scrittura e attesa del pool. Anche il test live crea richieste senza timeout.

**Verifica:** client configurato con 5 secondi di connessione e 120 di lettura;
la richiesta prodotta contiene tutti e quattro i timeout a `null`.
La semantica è confermata dalla [documentazione HTTPX](https://www.python-httpx.org/advanced/timeouts/).

**Correzione:** ereditare i default quando non esiste un override. Separare il
timeout di inattività fra chunk dalla durata totale del run: il primo non
limita una generazione che continua a produrre dati.

### R06 — P1 condizionale: il flag Secure del cookie viene scambiato per TLS

File: [bootstrap/settings.py](../../backend/src/newray/bootstrap/settings.py),
righe 46–49; [bootstrap/main.py](../../backend/src/newray/bootstrap/main.py), riga 24.
Riguarda A-07 e NewRay.md §20.3.

`bind_address=0.0.0.0` viene accettato se `cookie_secure=True`, ma Uvicorn parte
senza configurazione TLS. Secure regola l'invio del cookie dal browser, non
cifra il socket né autentica il bootstrap. La garanzia documentata «nessuna
esposizione remota senza TLS» non è quindi implementata da questa condizione.
Il default loopback e il Compose attuale non attivano questo caso.

**Verifica:** i settings accettano la combinazione; il percorso di avvio passa
a Uvicorn solo app, host e porta. Non ho aperto socket su interfacce remote.

**Correzione:** mantenere il vincolo loopback finché il deployment remoto non
è qualificato, oppure introdurre una modalità esplicita per TLS/proxy fidato
con verifiche coerenti. Il solo attributo del cookie non è una condizione sufficiente.

## Altri difetti concreti

### R07 — P2: lettura singola e lista scelgono versioni diverse del profilo

File: [profiles/adapters/postgres.py](../../backend/src/newray/modules/profiles/adapters/postgres.py),
righe 139–159. Riguarda B-02.

`_select` ordina le versioni in ordine crescente; `get_profile` sceglie
`rows[0]`, quindi la più vecchia. `list_profiles` seleziona invece il massimo.
Con una seconda versione, la lista può mostrare 1.0.1 mentre la risoluzione
usa 1.0.0 e il vecchio binding. Il fake usa correttamente il massimo: il test
unitario sulla versione recente non esercita il difetto dell'adapter SQL.

**Correzione:** un'unica regola di selezione, preferibilmente nella query;
portare il test delle due versioni anche nella suite PostgreSQL. Rilievo statico;
l'editing dei profili non ha ancora una API, come dichiarato nel backlog.

### R08 — P2: modello di default hardcoded e confronto letterale dei tag

File: [models/domain.py](../../backend/src/newray/modules/models/domain.py), riga 25;
[profiles/application.py](../../backend/src/newray/modules/profiles/application.py),
righe 90 e 147. Riguarda B-02, NewRay.md §§9.3/21.2/25.2.

Il servizio crea sempre un binding a `llama3.1`; non esiste configurazione di
provisioning per selezionarlo. La specifica esclude nomi hardcoded nei servizi
e rinvia il modello predefinito alla qualifica. In più, un catalogo che riporta
`llama3.1:latest` non corrisponde alla stringa del binding.

**Verifica:** catalogo sintetico con `llama3.1:latest`; tutti i profili risultano
senza modello e la risoluzione produce MODEL_UNAVAILABLE. Non è una prova di
quale nome restituirebbe un'istanza Ollama specifica qui non interrogata.

**Correzione:** provisioning esplicito del binding dal catalogo e identità
canonica/versionata del modello. Evitare qualsiasi fallback automatico.

### R09 — P2: lo snapshot frozen contiene riferimenti modificabili

File: [profiles/domain.py](../../backend/src/newray/modules/profiles/domain.py),
campo `ResolvedBinding.parameters`, riga 138;
[profiles/application.py](../../backend/src/newray/modules/profiles/application.py), riga 163.
Riguarda B-02 e il prossimo B-04.

`frozen=True` impedisce la riassegnazione del campo, non le modifiche al dict.
Il dict del binding viene passato direttamente allo snapshot. La prova che
assegna `snapshot.model_name` copre solo l'immutabilità superficiale.

**Verifica:** `snapshot.parameters['temperature'] = 999` riesce; nel fake
cambia anche il binding condiviso. Con PostgreSQL ciò non riscrive da solo il
record DB, ma lo snapshot in memoria resta modificabile.

**Correzione:** rappresentazione immutabile anche per i parametri annidati,
separata dai valori originari. Verificare mutazioni del chiamante e dello snapshot.

### R10 — P2: contratto dello stream Ollama incompleto nei casi terminali e di guasto

File: [models/adapters/ollama.py](../../backend/src/newray/modules/models/adapters/ollama.py),
righe 124–163; [models/ports.py](../../backend/src/newray/modules/models/ports.py).
Riguarda B-03; casi riprodotti con trasporto HTTP in memoria.

| Input/comportamento | Risultato osservato | Conseguenza |
| --- | --- | --- |
| `done: true, done_reason: length` | Completion con `finish_reason='stop'` | Il troncamento è descritto come arresto normale |
| EOF dopo un delta, senza `done` | Iteratore termina senza errore | Il consumer deve indovinare se il risultato è completo |
| Oggetto JSON con schema estraneo | Nessun evento e nessun errore | Non vale la garanzia di risposta non valida → errore tipizzato |
| ReadError dopo il primo chunk | Esce NetworkUnreachable, non DomainError | Il guasto attraversa la porta senza la traduzione promessa |
| `break` con riferimento al generatore ancora vivo | Stream aperto; si chiude con `aclose()` | Il contratto che equipara break e aclose è inesatto |

Ollama definisce il campo
[`done_reason`](https://docs.ollama.com/api/chat); i test del progetto usano
`finish_reason`, copiando l'assunzione sbagliata del parser. I casi qui elencati
non provano il comportamento GPU della cancellazione: verificano la connessione locale.

**Correzione:** fixture aderenti al protocollo effettivo; stato terminale unico
e obbligatorio; mapping dei guasti anche durante la lettura; contratto esplicito
di chiusura, con `aclosing`/`aclose` nel consumer. Servono test mirati ai casi
elencati prima di collegare l'adapter al worker.

### R11 — P2: query sincrone dentro le route asincrone dei profili

File: [profiles/application.py](../../backend/src/newray/modules/profiles/application.py),
righe 119–139 e 171; [routes/profiles.py](../../backend/src/newray/interfaces/http/routes/profiles.py).
Riguarda B-03 e la preparazione di B-06.

Le route sono async, ma il servizio chiama direttamente repository basati
sull'Engine SQLAlchemy sincrono. Durante le attese DB blocca quindi il ciclo
eventi che dovrà gestire anche gli stream. Inoltre la lista interroga Ollama
una volta per profilo; `resolve_binding` consulta due volte lo stesso catalogo.

**Evidenza:** percorso di chiamate statico; nessun benchmark di latenza eseguito.
**Correzione:** accesso DB asincrono oppure offload esplicito dei blocchi sincroni,
con confini transazionali preservati; una lettura del catalogo per operazione.

### R12 — P2: il controllo architetturale non impone alcuni vincoli dichiarati

File: [check_architecture.py](../../scripts/check_architecture.py), righe 165–178.
Riguarda A-06 e NewRay.md §6.2.

Gli import interni allo stesso modulo saltano il controllo, anche se dominio
o applicazione importano il proprio adapter. Il divieto di importare
`infrastructure` non include `module-core`.

**Verifica:** valutando le regole per `profiles/application.py`, entrambe le
dipendenze `newray.modules.profiles.adapters.postgres` e
`newray.infrastructure.network` producono zero violazioni.
Questo non significa che il codice attuale contenga questi import: significa
che il controllo non impedirà la regressione promessa dal backlog.

**Correzione:** controllare anche la direzione interna al modulo e aggiungere
test negativi permanenti del verificatore, distinti dai test del prodotto.

### R13 — P2: gli errori delle mutazioni non arrivano alla WebUI

File: [IdentityPage.tsx](../../web/src/pages/IdentityPage.tsx), righe 18–35.
Riguarda A-04 e NewRay.md §§3.3/18.3.

Il bootstrap gestisce soltanto CONFLICT; un errore 500 o di rete rimane nello
stato della mutation e non viene passato al pannello. La revoca non presenta
il proprio errore né il proprio stato pending. Il pannello usa solo lo stato
della query `/me`: il bottone può quindi sembrare non aver fatto nulla.

**Evidenza:** lettura del collegamento tra mutation e pannello; i test presenti
coprono l'errore della GET, non gli errori di queste POST.
**Correzione:** presentare pending/errori/recupero delle singole azioni e
aggiungere test di interazione per bootstrap e revoca falliti.

## Efficacia e limiti da rendere espliciti

**Accesso dopo il logout.** Il percorso HTTP riprodotto è: bootstrap 201 →
revoca 204 → nuovo tentativo sessione 409 → rilevazione sessione 401.
Non esiste ancora login/recovery: scadenza, cancellazione cookie o logout
lasciano l'utente senza un percorso normale di rientro. Il login futuro è
menzionato nel codice, quindi non considero la sua assenza una regressione
accidentale; rende però impropria la promessa di conflitto «recuperabile».
Prima di dati utili serve almeno un recupero locale autenticato e documentato.

**Origine e CSRF.** Il rinvio del controllo completo all'ufficio è documentato,
ma manca una verifica server-side dell'origine anche per mutazioni locali.
La probe con cookie valido e Origin `http://127.0.0.1:9999` sulla revoca di
`http://127.0.0.1:8000` riceve 204. È una prova di accettazione lato server,
non un exploit browser end-to-end. Va colmato il requisito di NewRay.md §19.2
prima di considerare qualificata la superficie autenticata.

**Errori pubblici.** Un titolo vuoto restituisce 422 con solo `detail`, non
`code/message/retryable/correlation_id`. Il gestore applicativo copre errori
di dominio e generici, non quelli di validazione FastAPI. Il client finisce
per descrivere un input non valido come errore interno. Anche i generici 500
vengono tutti marcati retryable, cosa da rivedere prima di effetti non idempotenti.

**Contratto ChatModel.** Contiene soltanto testo e completamento: non include
tool call, risultati tool o capability del modello generativo. È un primo
percorso testuale utile, ma copre solo parte della porta minima di NewRay.md
§6.1. Gateway e contratti delle estensioni, previsti in A/B, meritano attività
esplicite nel backlog prima di usare strumenti. La chat completa B-04–B-09,
RAG e posta non sono stati valutati come funzionalità mancanti già dovute.

**Deployment e documentazione.** Il Compose non è stato qualificato live;
usa un ruolo migrazioni creato come superuser dell'immagine, a differenza del
ruolo owner non superuser dei test. Le immagini `pg16` e `python:3.12-slim`
sono tag mobili. Sono divergenze da risolvere nella qualifica di distribuzione,
non una prova che l'isolamento del ruolo applicativo sia già stato violato.
I file backend/AGENTS.md e web/AGENTS.md dicono ancora che non esistono codice
o test; la tabella generale segna B «Da fare» nonostante tre sottoattività
completate. Occorre allineare istruzioni e stato reale.

## Valutazione rispetto al backlog

| Area | Valutazione |
| --- | --- |
| Struttura e manutenibilità | Buona base: responsabilità riconoscibili, dominio leggero, porte, adapter e composizione separati |
| Controlli ordinari | Buona disciplina; test rapidi, tipi strict, contratti senza drift e build ripetibile nell'ambiente disponibile |
| Robustezza | Da consolidare: transazioni, concorrenza, ripristino da guasti e protocollo reale non hanno copertura sufficiente |
| Efficacia per l'utente | Dimostrato il percorso tecnico iniziale; non ancora uso continuativo né workflow Assistente completo |
| Allineamento alle premesse | Buono nella direzione; parziale nelle garanzie di snapshot, configurabilità, confini verificati e deployment |

I punti forti da preservare sono la distinzione principal/profilo AI, lo scope
esplicito, RLS/FK composite, il dominio senza framework, il codegen dai DTO e
l'assenza di router LLM/fallback invisibili. Non ho riscontrato nel percorso
letto una ragione per introdurre microservizi o altri framework di orchestrazione.

Rivedrei le evidenze di chiusura di A-02/A-03/A-04/A-06/A-07 e B-01/B-02/B-03
alla luce dei rilievi pertinenti. Non occorre azzerare il lavoro: registrare
le correzioni in docs/backlog.md e legare la nuova chiusura a prove dei casi
qui mancanti. A-01 è sostanzialmente sostenuto dai manifest/lock presenti;
questa revisione non è un audit legale o di vulnerabilità delle dipendenze.

L'ordine consigliato è: rendere sicuri i test di migrazione; sistemare le
transazioni e la sequenza dei messaggi; correggere binding/snapshot e adapter
Ollama; allineare errori UI e controlli automatici; poi proseguire B-04.
