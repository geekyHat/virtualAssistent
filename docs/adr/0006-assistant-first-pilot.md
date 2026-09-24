# ADR 0006 — Pilot centrato sull'Assistente locale

Data: 2026-09-23. Stato: adottato come direzione di prodotto; implementazione
e qualificazione tracciate soltanto in [backlog.md](../backlog.md).

## Contesto

Dopo la prova della GUI l'utente richiede meno configurazione esposta, un
solo assistente utile, sicurezza dei documenti, memoria, immagini e capacità
di migliorare il proprio metodo. Il vecchio piano anticipava un catalogo
multimodello e profili specialistici prima del workflow quotidiano completo.

## Decisione

1. Il pilot provisiona solo Assistente, con binding esplicito al candidato
   Gemma 4 31B IT UD-Q4_K_XL. Identificare artefatti, licenza, digest e runtime;
   qualificare testo, chiamate tool e visione separatamente. Nessun download,
   fallback o sostituzione automatica autorizzati da questo ADR. I pesi da soli
   non definiscono memoria necessaria, velocità o supporto immagini effettivo.
2. Profili specialistici e scelta modello restano un'estensione manuale
   avanzata. Nuove installazioni senza Coder/Researcher; sugli impianti esistenti
   preservare dati, versioni e snapshot. Il cambio binding richiede un'operazione
   esplicita e non muta run storici o in corso. Nessuna cancellazione dei pesi.
3. WebUI chat-first: centro per conversazione/composer/allegati; destra per
   navigazione modulare. Conservare React/Vite, Tailwind/shadcn, contratti,
   accessibilità e i18n. Non riscrivere il frontend per ragioni estetiche.
4. Sicurezza tramite principal, policy server-side, grant minimi e isolamento
   OS dei tool/parser. Il modello propone azioni ma non attribuisce permessi.
   Cloud disabilitato; lettura locale ed esportazione sono autorizzazioni diverse.
5. Memoria esplicita con provenienza, rettifica, scadenza e cancellazione in
   PostgreSQL; retrieval documentale separato. pgvector/FTS è la base, embedding
   locale ammesso. Il plugin Memory LanceDB di OpenClaw è un riferimento da
   confrontare, non un'integrazione già selezionata. Un secondo storage richiede
   vantaggio misurato e decisione esplicita; Mem0 non è obbligatorio.
6. Apprendimento procedurale: evidenza verificabile → proposta versionata →
   test → approvazione → attivazione revocabile. Primo percorso manuale e
   delimitato; review periodiche successive. Niente training dei pesi,
   auto-modifica del backend, ampliamento dei grant o critic sincrono.
7. Composizione deterministica del contesto senza riscrittura silenziosa
   dell'intento. Von è differito: eventuale esperimento non abilita routing
   fra modelli/profili e non aggiunge un coordinatore LLM.
8. Un workflow locale finito, con stato e ricevute NewRay. Qualificare Lobster
   come adapter opzionale: nessuna shell generica, autorità parallela o secondo
   scheduler. Se incompatibile, usare il motore durevole esistente per quel solo
   workflow, senza costruire un framework generico alternativo.

## Decisioni sostituite e mantenute

Sostituisce in NewRay.md §§1/3/5/9/11/23/25 il provisioning di tre profili,
la priorità del catalogo multimodello e la sequenza che anticipava web/email
e authoring completo. Negli esempi di alberi e pack, Coder/Researcher e tool
cloud sono target futuri, non default da materializzare.
Sostituisce in §16 la preferenza iniziale obbligata per l'adapter Mem0.
Aggiorna ADR 0003 soltanto nella priorità: memoria esplicita e prima proposta
procedurale entrano nel pilot; apprendimento avanzato resta successivo.

Restano ADR 0001/0002/0004/0005 e le invarianti ADR 0003: monolite modulare,
PostgreSQL autorevole, un modello generativo nel run e nelle continuazioni,
niente Butler/router/judge/fallback, gateway, scope revocabili, contratti
generati e vertical slice reali. La sicurezza multi-principal resta obbligatoria
anche se il primo impiego è personale. Run/worker durevoli non sono rinviati.

## Conseguenze e uscita

Il pilot deve provare chat persistente/stop/reconnect, documenti con fonti,
immagini sul candidato esatto, memoria correggibile/dimenticabile, un workflow
locale e una proposta di skill controllata. Nessuna etichetta «pronto» prima
dei gate live specifici. Un limite del candidato produce un no-go esplicito
o una decisione utente sul perimetro, non una sostituzione nascosta.

Cloud, profili esperti, catalogo plugin aperto, automazioni periodiche,
authoring completo, web/email/calendario e ufficio avanzato sono differiti.
Le proposte di API nei ticket non diventano contratti implementati finché
DTO/schema/client e prove della slice non sono consegnati.

## Riferimenti

- Richiesta dell'utente del 23 settembre 2026 e successiva autorizzazione al piano.
- [Specifica vigente](../../NewRay.md) e [archivio integrale del piano precedente](../../todos.bak).
- Candidati da ispezionare/pinnare prima dell'uso: [OpenClaw](https://github.com/openclaw/openclaw),
  [Lobster](https://github.com/openclaw/lobster), [Von](https://github.com/wfzyx/von),
  [Hermes Agent](https://github.com/NousResearch/hermes-agent).
  Questi riferimenti non attestano compatibilità, licenza verificata o qualifica.
