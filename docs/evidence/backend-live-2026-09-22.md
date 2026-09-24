# Backend e live Ollama — 22 settembre 2026

Questa evidenza chiude il lavoro backend immediato B-03.2-34, il contenimento
B-03.2-36 e l'hardening B-03.2-22. Non qualifica la qualità del modello e non
chiude i run durevoli B-04–B-07.

## Perimetro verificato

- Owner: `models` per la telemetria vendor, `runs` per l'orchestrazione della
  preview, `conversations` per la coppia atomica di messaggi e `identity` per
  scope/RLS e revoca.
- Principal: sempre derivato dalla sessione server; nessun ID utente o
  organizzazione accettato dal payload del run.
- Dati/effetti: cronologia privata, prompt, risposta, ricevuta idempotente e
  metriche di generazione. Nessun effetto esterno, account o telemetria remota.
- Limiti preview: 50 messaggi recenti, 100.000 caratteri di contesto e 2.048
  token output. Errore, EOF e cancel non persistono il prompt; `stop` e
  `length` persistono atomicamente user+assistant.

## PostgreSQL e gate backend

- PostgreSQL 18.6 effimero su loopback; ruolo applicativo distinto dal
  migratore e senza `BYPASSRLS`.
- Suite integration: **50 passed**. Include migrazione 0007, RLS organizations,
  revoca scoped, due principal e preview reale su PostgreSQL.
- Suite backend completa: **323 passed, 2 skipped, 2 warning** in 56,82 s. I
  due skip sono esclusivamente i test Ollama opt-in, eseguiti separatamente.
- Ruff lint e format verdi; mypy verde su 65 sorgenti; checker architettura
  verde su 65 file; codegen `--check` senza drift.
- Warning noti: deprecazioni delle integrazioni Starlette/httpx e alias AnyIO;
  nessun fallimento funzionale.

## Prova live Ollama

- Runtime: Ollama **0.34.2**, endpoint loopback.
- Modello: `llama3.2:3b`.
- Digest catalogo:
  `a80c4f17acd55265feec403c7aef86be0c25983ab279d83f3bcd3abbcb5b8b72`.
- Hardware inference: AMD Radeon AI PRO R9700, gfx1201, 34.208.743.424 byte
  VRAM. Al momento della prova 33.746.284.544 byte erano occupati anche da
  altri carichi; il modello scelto evita di interferire con quei processi.
- Corpus/prompt sintetico: singolo messaggio italiano `Rispondi solo: ok`,
  `think=false`, massimo 32 token. Nessun documento o dato privato.
- `tests/live/test_ollama.py`: **2 passed** in 11,62 s; presenza esatta
  nome+digest, contenuto visibile, terminale, conteggio output, durata positiva
  e formula token/s verificati attraverso l'adapter NewRay.
- Sonda numerica separata sullo stesso runtime/artefatto: `done=true`,
  `done_reason=stop`, `eval_count=9`, `eval_duration=498298000 ns`,
  `tokens_per_second=18.06`. È una misura del singolo campione, non un benchmark.

Il modello piccolo è stato scelto soltanto per il contratto live sotto forte
contesa VRAM. Non diventa per questo `qualified`; B-03.4 e B-09.3 conservano i
propri corpus, workflow e criteri di qualifica.

## Fingerprint dei confini principali

La directory non è un worktree Git; questi SHA-256 identificano i file chiave
eseguiti nella prova:

```text
694cb5c7caf68d9acb1220d0400fad923c712c200367fb2411c99dcfdc1dd1e0  backend/src/newray/modules/models/domain.py
c9466ea16a4352b74e727b14bd9de628455c510509769b36d88b56fe22d804f0  backend/src/newray/modules/models/adapters/ollama.py
4fbf37be9821857062438ffa5a733982e6b95f5a90c8f726a81c247234a69dde  backend/src/newray/modules/runs/application.py
172c27e823f7ce91fc5425d214a2910b8c9e3fead09a20d23d3d31f368c68242  backend/src/newray/interfaces/http/routes/chat.py
aa0541f388b0979f5acd9ea14ac497a937aef40d59ecfedb0582cecd1b73cfc4  backend/migrations/versions/0007_identity_scope_hardening.py
c38ac7573a56717d7d0bf3e49ccc3d417709ad4e8ac6805ebaf65708c190c0b5  contracts/openapi.json
```
