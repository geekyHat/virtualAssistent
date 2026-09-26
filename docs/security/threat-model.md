# Threat model — pilot NewRay

Revisione: **26 settembre 2026**. Copre il perimetro attualmente
implementato nel pilot Assistente (P-01 → P-06, P-19). Superfici non
ancora costruite (allegati, immagini, RAG, skills, workflow) sono
citate come **rimandate**, senza controlli né promesse.

## Perimetro

Il pilot è **local-first** su Linux, un solo profilo generativo,
PostgreSQL 16+ con `pgvector`, Ollama in loopback, cloud disabilitato.
La superficie di attacco esterna è il singolo processo API su loopback
per default; l'origine pubblica canonica è validata dai settings.

## Attori

| Attore | Descrizione | Livello di fiducia |
| --- | --- | --- |
| `owner` | Proprietario, unico su un'installazione monouso del bootstrap | Alto |
| `member` | Ipotetico secondo utente della stessa organizzazione (non attivato nel pilot; supportato dallo scope) | Medio |
| `stranger` | Utente di altra organizzazione | Nessuno |
| `anonimo` | Nessuna sessione presentata | Nessuno |
| `app_role` | Ruolo DB `newray_app` (senza BYPASSRLS) | Vincolato dalla RLS |
| `worker` | Processo che consuma la coda `runs` sotto `app.worker_id` | Elevato ma limitato al proprio contratto |
| `rete_esterna` | Host non locali raggiungibili tramite un URL fornito dal client | Zero — il pilot rifiuta egress non locale |

## Bene protetto

Dati per principal (conversazioni, messaggi, run, eventi, snapshot,
partial, ricevute di idempotenza). Ricevute di sessione. Credenziali
locali (Argon2id). Snapshot congelato del run (input, binding, digest,
messaggi, parametri). Metriche autorevoli dell'esito.

## Superfici attive e minacce coperte

### Identità e sessione (P-02, P-04)
- Cookie di sessione opaco `newray_session`, HttpOnly, SameSite=Lax.
- Bootstrap monouso; login riusa la ricevuta dell'owner.
- Header/parametri client (`X-User-Id`, `X-Role`, ecc.) mai letti.

**Minacce**
- Cross-user via manipolazione ID nel payload → Principal risolto dal
  server dal cookie: mai dai valori nel body/URL.
- Session fixation su revoca → revoca invalida la sessione DB e ogni
  presentazione successiva del cookie fallisce.
- CSRF baseline → SameSite=Lax + validazione `public_origin`.

### Isolamento per scope (RLS FORCE)
- Tabelle `users/organizations/sessions`, `conversations`,
  `messages`, `profiles`, `profile_versions`, `model_bindings`,
  `runs`, `run_events` con RLS FORCED.
- Policy per principal (`app.user_id`+`app.organization_id`); worker
  usa policy separata `app.worker_id <> ''`.
- Migration owner (`newray_migrate`) è distinto dall'app role.

**Minacce**
- Read/write cross-org via query diretta → policy nega su GET/UPDATE.
- Payload manipolato con id di un'altra organizzazione → repository
  in POST/PATCH resolve via scope, PostgreSQL nega la riga fuori scope.
- `INSERT` non intenzionale dal client → grants column-level negano
  UPDATE su snapshot/`id` e su campi non pertinenti al canale utente.

### Run durevoli e coda (P-05)
- Ricevuta idempotente scoped (`org_id+owner_id+conversation_id+idempotency_key`).
- Snapshot JSONB immutabile: solo INSERT concesso; UPDATE su `snapshot`
  rifiutato dal grant.
- Lease + fence: il worker precedente non può finalizzare dopo un reclaim.

**Minacce**
- Idempotency-key rubata → chiave unica per scope; utente diverso non
  vede la ricevuta e non può replay-attaccarla.
- Ricevuta riutilizzata con payload diverso → 409 CONFLICT.
- Race di due worker → un solo claim vince (SKIP LOCKED); fence bumpato
  invalida ogni finalize del vecchio worker.
- Cancellazione persistita (P-06) → idempotente; `cancel_requested_at`
  visibile al worker al prossimo checkpoint.

### Eventi durevoli e stream SSE (P-06)
- Outbox `run_events` immutabile (solo SELECT/INSERT concessi).
- Sequence monotona per run (`UNIQUE (run_id, sequence)`), cursore
  `after` client-side, `Last-Event-ID` prevale.
- Autorizzazione risolta prima dello stream.

**Minacce**
- Lettura degli eventi di un altro utente → RLS nega, la route ritorna
  404 uniforme prima dello stream.
- Race cancel/done → il fence rende il finalize del vecchio worker
  non applicabile; il cancel dopo done è idempotente e non muta
  lo stato terminale.

### Qualifica del modello (P-19)
- Store file-based per host in `<data_dir>/qualifications/`.
- Nome del file validato da regex safe (nessun path traversal).
- Rapporto atomico con `run_id` fresco, mai riusato come successo.
- Preflight non concede `qualified` — solo `run_campaign` scrive un
  record dopo aver osservato che tutte le sonde sono passate.

**Minacce**
- File manipolato manualmente → `model_name` nel file diverso dal nome
  del file → `ValueError`, nessun successo silenzioso.
- Cambio digest/hardware → `applies_to()` decade, capacità qualificate
  ritornano vuote.

### Egress di rete
- `HttpClient` prende un `base_url` esplicito: la validazione lato
  settings garantisce loopback per il runtime Ollama e HTTP(S) per
  l'origine pubblica.
- Nessun redirect seguito dal client HTTP (default `httpx`).
- `SameOriginMiddleware` respinge origini estranee alle API dell'app.
- Bind non-loopback richiede TLS e `cookie_secure=true`.

**Minacce**
- URL fornito con schema o host non locale → validatore
  `NEWRAY_OLLAMA_BASE_URL` rifiuta.
- Redirect verso host esterno → non seguito.
- Bind LAN in HTTP semplice → settings rifiutano all'avvio.

## Superfici rimandate

- Allegati / archivio file (P-08)
- Estrazione documenti e provenienza (P-09)
- Visione end-to-end (P-10)
- Retrieval locale con embedding (P-11)
- Composizione contesto (P-12)
- Memoria esplicita / dimenticare (P-13)
- Workflow locale (P-14/15)
- Skill proposte da esperienza (P-16)
- Distribuzione locale e supply chain (P-18)

Il threat model **si estende** con le loro slice, non anticipa
promesse: la matrice dei controlli marca queste righe come "rimandato",
non "coperto".

## Falso verde e controllo negativo

Un test avversariale che passa può essere un test sbagliato: la suite
include **controlli negativi** per le proprietà RLS/scope più
importanti. Un controllo negativo simula un bypass della protezione e
verifica che il test avversariale corrispondente si attivi (fallisca)
in quello scenario: se non fallisce, il test è cieco. Vedi
`docs/security/controls.md` per la lista.
