# ADR 0008 — Gateway tool come autorità server-side, ciclo guidato dal worker

Data: 2026-09-24. Stato: adottato per P-07. Fonte: NewRay.md §§8.1, 8.3,
10.3, 7; ADR 0001 (monolite modulare), ADR 0003 (esecuzione diretta, nessun
router LLM), ADR 0007 (ruolo interno del worker).

## Contesto

P-07 introduce le chiamate tool nel run generativo (NewRay.md §8.1 passo
6–7). Serve decidere: (a) chi ha l'autorità sull'esecuzione di un tool
(allowlist, schema, grant), (b) come il modello richiede un tool e come la
continuazione avviene "sullo stesso modello" senza router (ADR 0003), (c)
dove vivono i confini di modulo per non creare un ciclo `runs`↔`tools`.

NewRay.md §10.3 impone che il toolset sia l'intersezione fra capacità del
profilo, tool installati e grant del principal, e che gli argomenti del
modello siano dati non fidati che non scelgono principal o path. §8.3
richiede una ricevuta `tool_invocation` con stati distinti e idempotente.

## Decisione

1. **Autorità nel gateway, non nel modello.** Il modulo `tools` possiede un
   `RegistryToolGateway`: registry per ID stabile (allowlist), validazione
   degli argomenti contro lo schema, e intersezione capacità/grant. Il testo
   e gli argomenti del modello non concedono privilegi (§10.3): un tool
   sconosciuto o uno schema non valido produce un esito d'errore esplicito,
   mai un'esecuzione. Lo scope dell'esecuzione è quello del principal,
   iniettato dal gateway; un argomento del modello (es. `run_id`) non lo
   sceglie — un `run_id` altrui risolve a "non trovato" sotto RLS.

2. **Ciclo guidato dal worker, un solo modello.** Il modello emette una
   `ToolCallRequest` nello stream; il worker (in `runs`) la esegue tramite il
   gateway, reimmette il risultato come messaggio `TOOL` e continua con lo
   **stesso** modello/snapshot (ADR 0003: nessun coordinatore, nessun router).
   Limiti espliciti: turni tool per run (`tool_budget_exceeded`), deadline e
   cancellazione propagate al ciclo, fencing invariato.

3. **Inversione di dipendenza per evitare il ciclo di modulo.** La porta
   `ToolGateway` e il tipo `ToolResult` vivono in `runs` (il consumatore); il
   gateway concreto vive in `tools` e importa `runs` (per lo store di sola
   lettura del tool `run.status`). `runs` non importa `tools`; il bootstrap
   collega l'implementazione. Nessun ciclo `runs`↔`tools`.

4. **Ricevuta ed eventi fencing-checked.** La ricevuta `tool_invocations`
   (idempotente per `(run_id, call_id)`) e gli eventi `tool.*` sono scritti
   dalla funzione `SECURITY DEFINER` `newray_record_tool_event`
   (proprietà `newray_scheduler`, ADR 0007), nello stesso commit
   fencing-checked del run `running`: solo il worker con il fence corrente
   scrive. La ricevuta `executing` precede l'esecuzione, così una perdita di
   fence a metà lascia comunque traccia.

## Conseguenze e limiti

- Il primo tool è `run.status`, locale e di sola lettura: nessun effetto
  esterno, nessuna approvazione. `read_only` nello spec segnala l'assenza di
  effetti.
- **Gated (non in P-07):** l'approval flow (`awaiting_approval`, rilascio e
  riacquisizione del compute lease, approvazione vincolata a
  payload/versione/scadenza e consumata atomicamente, revoca fra
  approvazione ed effetto) e il layer dei grant per tool con capacità/effetti.
  Non esiste ancora un tool con effetti esterni da approvare (P-11/P-15):
  costruirli ora sarebbe un modulo per un consumer inesistente (AGENTS.md).
  Gli stati `prepared`/`awaiting_approval`/`outcome_unknown` restano nel
  vocabolario dello schema per stabilità, non scritti in questo pilot.
- **Live tool use in P-20:** l'adapter Ollama non emette ancora
  `ToolCallRequest` (il ciclo è provato con un `ChatModel` fake); il toolset
  è comunque offerto nella richiesta. L'uso reale dei tool da parte di Gemma
  è qualificato in P-20, non dedotto dal fake (coerente con il ticket P-07).
- Nessuna shell generica né MCP server arbitrario: i futuri adapter tool si
  aggiungono a questa autorità, non la bypassano (NewRay.md §10.3).
