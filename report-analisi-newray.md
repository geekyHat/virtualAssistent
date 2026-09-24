# NewRay — verifica indipendente del report di Anne

Data: 21 settembre 2026. Fonte: report esterno incollato dall'utente nella
conversazione. Questo documento ne valuta le conclusioni sul codice locale:
non è una copia verbatim del report e non attribuisce ad Anne le nuove prove.
Stati, priorità e criteri di chiusura restano esclusivamente in
[docs/backlog.md](docs/backlog.md).

## Verdetto

I primi tre rilievi sono confermati, con precisazioni su impatto e rimedi.
Il quarto individua correttamente una porta senza scope, ma descrive in modo
impreciso l'effetto di una futura RLS. Il giudizio «tutti i gate eseguiti»
non è sostenuto dal report: mancano le prove frontend/browser, e l'integrazione
ha un fallimento. La nuova esecuzione browser rileva inoltre un caso instabile.

Non sono state applicate patch al codice o ai test applicativi: questa
consegna aggiorna la valutazione e la pianificazione, come richiesto.

## Validità dei quattro finding

### 1. Nome owner e lockout — confermato, impatto da precisare

In [application.py](backend/src/newray/modules/identity/application.py),
`bootstrap_owner` salva il nome senza trim, mentre `login` cerca quello
stripato. DTO e dataclass non rifiutano nomi composti solo da whitespace.

Riproduzione con IdentityService, fake repository condivisi e credenziale
sintetica: bootstrap con nome `  Synthetic owner  ` conserva gli spazi;
login con nome pulito **e con quello originale** restituiscono entrambi
`InvalidCredentials`. Anche un bootstrap con nome composto solo da spazi riesce.

Precisazioni:

- La WebUI attuale fa già trim in IdentityPanel e LoginPanel; non tutti gli
  utenti browser sono esposti. Il difetto è al confine server e riguarda
  chiamate API dirette e record esistenti con questi nomi.
- Non serve aspettare 30 giorni: logout o perdita del cookie bastano. Il
  lockout riguarda il percorso applicativo, non l'impossibilità assoluta di
  una riparazione amministrativa.
- Normalizzare le sole nuove scritture **non ripara gli owner già salvati**.
  Servono compatibilità/migrazione o recupero esplicito, preservando ID e dati.
- Il helper proposto è nel livello applicativo, non nel dominio come detto
  dal report; definire la semantica di errore per input vuoto insieme ai DTO.

Tracciamento: B-03.2-30, dipendenza della chiusura B-03.2-14.

### 2. Test upgrade storico — confermato anche su PostgreSQL reale

[test_migrations.py](backend/tests/integration/test_migrations.py), test
`test_upgrade_0003_popolata_preserva_sequenze_e_alloca_la_successiva`, effettua
downgrade a 0003 e poi invoca il bootstrap corrente, il cui INSERT include
`credential_hash` introdotto solo in 0005. Riprodotto `UndefinedColumn`.

La diagnosi di regressione del test è corretta: questo fallimento non prova
un difetto del backfill in produzione. Il gate però non verifica più il caso
che dovrebbe proteggere. La proposta di SQL storico è corretta; conviene
mantenere tutte le fixture pre-upgrade indipendenti dagli adapter head.
L'INSERT conversazione corrente è compatibile oggi, ma non è una garanzia futura.

Tracciamento: B-03.2-29; conservate le evidenze storiche di B-03.2-04,
esplicitando che la verifica corrente è rossa. Nessuna modifica a 0004/0005
di produzione è giustificata da questo errore del test.

### 3. Differenza temporale del login — confermata strutturalmente

Una sonda con `unittest.mock.patch.object`, che avvolge il verificatore reale,
osserva zero invocazioni per nome sconosciuto e una per nome corretto con
password errata. È evidenza della differenza di lavoro, non una misura di
sfruttabilità remota o un benchmark in millisecondi.

Il confronto finale a tempo costante non rende costante il percorso HTTP.
La verifica dummy proposta affronta la differenza principale, ma non
garantisce latenze identiche. Definire anche protezione da abusi e costo
coerente con la libreria scelta; evitare test CI fondati su soglie temporali.

L'anti-enumerazione non è assoluta neppure nei codici: un owner legacy senza
hash restituisce `CREDENTIAL_NOT_SET`; `/session/status` dichiara intenzionalmente
se l'installazione è inizializzata. Queste eccezioni vanno documentate nel
contratto, senza presentarle come bypass di autenticazione.

Tracciamento: B-03.2-31. Corretta anche l'affermazione di uguaglianza temporale
precedentemente presente nelle evidenze del backlog.

### 4. Organizzazioni senza scope — fatto confermato, rischio riformulato

`get_organization` in [ports.py](backend/src/newray/modules/identity/ports.py)
e nell'adapter PostgreSQL non riceve Scope né imposta il contesto. Non ha
chiamanti applicativi in `backend/src`; esiste anche l'implementazione fake.
0001 concede SELECT/INSERT su organizations senza abilitarvi RLS.

Il rischio corrente è il contratto incoerente e la lettura DB non isolata,
non un exploit HTTP dimostrato. Con una policy RLS che richieda contesto,
una lettura senza contesto normalmente verrebbe negata/filtrata: non crea
automaticamente un «buco» futuro. Documentare soltanto l'assenza di RLS non
soddisfa ADR 0002; rimuovere la porta inutilizzata non protegge da solo la tabella.

Tracciamento nei ticket già aperti B-03.2-22 (scope/RLS) e 28 (porte inutilizzate),
senza duplicarli. Il lookup pre-auth introdotto in 0005 è un'eccezione di
lettura governata dall'applicazione: il flag di transazione non è una credenziale
e la policy SELECT aggiuntiva non va confusa con una policy SQL RESTRICTIVE.

## Ulteriori problemi emersi nel riesame

### Recupero e scelta della libreria credenziali

B-03.2-14 risultava completato pur dichiarando il recovery legacy ancora TODO.
Il codice segnala `CREDENTIAL_NOT_SET`, ma manca il percorso operativo che
imposta una credenziale senza perdere i dati. Il ticket torna in corso.

[NewRay.md §4.2 e §20.3](NewRay.md) prescrive pwdlib/Argon2 e hashing tramite
libreria mantenuta. L'implementazione in
[kernel/crypto.py](backend/src/newray/kernel/crypto.py) usa primitive standard
PBKDF2 con formato e gestione propri; il manifest non include pwdlib.
È una **deviazione dalla decisione di progetto**, non la prova che PBKDF2 sia
insicuro. Nessun ADR letto la sostituisce. B-03.2-32 pianifica allineamento e
compatibilità degli hash esistenti; un'alternativa richiede decisione esplicita.

### Browser: doppia richiesta intermittente

`npm run e2e` ha prodotto **11 passed, 1 failed**: il caso doppio click in
[session.spec.ts](web/e2e/ui/session.spec.ts) conta due POST invece di uno.
Ripetuto isolatamente cinque volte, passa **5/5**. Non si può quindi liquidare
il fallimento né dichiarare una causa certa; B-03.2-33 richiede diagnosi di
componente, transizione asincrona e fixture, senza aumentare sleep/retry per
nasconderlo. Riaperta la verifica di B-03.2-13 e B-09.1.

Possibile finestra da investigare: il successo della mutation avvia
invalidazioni con `void`, senza attenderle; `isPending` può terminare prima
della conferma della nuova identità. Il test stesso dipende da ritardi e
click forzati. Questa è un'ipotesi da isolare, non un fix già verificato.
Due POST al trasporto mock **non dimostrano due owner persistiti**: il server
mantiene bootstrap atomico/monouso e i relativi test PostgreSQL passano.

## Controlli realmente eseguiti nel riesame

| Controllo | Esito del 21 settembre |
| --- | --- |
| Backend `.venv/bin/python -m pytest -q` | 212 passed, 46 skipped, 6,95 s |
| Backend `.venv/bin/ruff check src tests` (path equivalenti dal root) | Verde |
| Ruff format check backend | 97 file già formattati |
| Backend `.venv/bin/mypy src` | Verde, 58 file |
| `scripts/check_architecture.py` con Python backend | Verde, 58 file |
| `scripts/generate_contracts.py --check` con Python backend | Verde, nessun drift |
| PostgreSQL 18.6 temporaneo, `pytest tests/integration -q --tb=line` | 43 passed, 1 failed, 32,69 s; finding 2 |
| `web`: `npm run check` | Format/lint/typecheck, 17 test, licenze 341 pacchetti, build e budget verdi |
| `web`: `npm run e2e` | 11 passed, 1 failed, 6,6 s; doppio POST |
| `web`: `npm run e2e -- --grep 'doppio click' --repeat-each=5` | 5 passed, 17,2 s; non smentisce l'instabilità |
| Sonde IdentityService con fake condivisi | Nome padded e whitespace-only riprodotti; verificatore 0/1 chiamate |

L'integrazione ha usato il provisioning del launcher in una nuova directory
temporanea, ambiente NEWRAY ripulito, ruoli admin/migrate/app separati e DB
sacrificabili delle fixture. Le tre variabili NEWRAY_TEST sono state generate
solo per il processo di test; nessun DSN operativo o `.env` letto. Cluster
arrestato e dati sintetici temporanei rimossi al termine. Nessun dato utente
è stato migrato, cancellato o modificato.

Per ripetere in un ambiente di prova già predisposto: impostare
`NEWRAY_TEST_ADMIN_URL`, `NEWRAY_TEST_MIGRATION_URL` e
`NEWRAY_TEST_DATABASE_URL` sui rispettivi ruoli del **cluster di test**, quindi
eseguire il comando integration della tabella da `backend/`. Non usare il DB
personale per tentare la riproduzione del downgrade.

I 46 skip offline sono **44 test PostgreSQL + 2 live Ollama**, non 46 test DB.
Il criterio post-fix corretto è zero fallimenti e zero skip inattesi nelle
suite richieste: non «tutta la suite senza skip» senza configurare anche i
live. Ollama, browser live→API/DB e CI remota non sono stati eseguiti qui.
I test browser `ui` intercettano HTTP e non qualificano l'intera catena reale.
Restano i warning di deprecazione Starlette/httpx e AnyIO; non sono errori.

## Identificazione del codice esaminato

La directory non è un repository Git disponibile (`git status` fallisce).
Fingerprint SHA-256 prima di qualsiasi modifica documentale:

| File | SHA-256 |
| --- | --- |
| `backend/src/newray/modules/identity/application.py` | `451dd49aaaaa7bb03db949721006f4f35e707a11345afe15b8a216c8116d7e73` |
| `backend/src/newray/kernel/crypto.py` | `2255395c120a4fe98e413914cb1642df65940f94665ed2936489f6465e80bd6b` |
| `backend/tests/integration/test_migrations.py` | `209062481eda40c9d8a6d4763edcdb6bc090bace52149f67c72f2b4b4a431ec1` |
| `backend/migrations/versions/0005_identity_credentials.py` | `e975a398ce15588d9146887b2c79a783a1d96b8ec91237367028ee02625d5456` |
| `web/src/app/App.tsx` | `3258cc741768f953a798ca50130765b766c54ebc464aeff06346b4ddcf426db5` |

## Effetto sulla pianificazione

Il backlog distingue ora contratti/dati/runtime backend da pagine/client/
reducer frontend, mantenendo prove integrate e dipendenze. Il login esistente
è riconosciuto; chat e selettore modello restano da costruire. Le evidenze
storiche sono conservate ma non usate per nascondere i gate oggi non verdi.
