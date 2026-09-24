# WebUI E2E — Playwright (B-09.1)

Due profili distinti (`playwright.config.ts`):

- `ui`: browser reale, **backend simulato** dalla fixture `session` in
  `fixtures.ts`. Nessun servizio esterno; nessun database; nessuna GPU.
  Fa girare la sola build Vite dev su `127.0.0.1:$NEWRAY_E2E_UI_PORT`
  (default 4318). Eseguito in ogni PR.
- `live`: browser reale contro `.start` con PostgreSQL/Ollama veri.
  **Opt-in** con `NEWRAY_E2E_LIVE=1` e `NEWRAY_E2E_LIVE_BASE=…`.
  Non parte in CI; è l'ingresso di B-09.2/B-09.3.

## Comandi

```bash
# Solo installazione binari Chromium (una tantum)
npm run e2e:install

# Profilo ui su trasporto controllato
npm run e2e

# Profilo live: prima avvia `.start`, poi:
NEWRAY_E2E_LIVE_BASE=http://127.0.0.1:5173 npm run e2e:live
```

## Convenzioni

- Le rotte mockate stanno solo in `fixtures.ts`; un test può sovrapporre
  la fixture con `context.route(...)` (l'ultimo route registrato vince).
- Ogni test naviga esplicitamente: `beforeEach` non fa `goto` per evitare
  la corsa fra la prima `GET /me` in flight e la modifica di scenario.
- Nessun account personale nel profilo `live`. Il test bootstrap crea un
  owner sintetico e salta se il cluster è già inizializzato.
- Gli artefatti Playwright (`test-results/`, `playwright-report/`) sono
  ignorati da git/prettier; in CI sono caricati come artifact su fallimento.
