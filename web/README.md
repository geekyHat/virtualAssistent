# WebUI

SPA React, TypeScript strict e Vite (NewRay.md §18). Nata in A-04 la prima
pagina collegata: sessione e bootstrap dell'installazione locale
(`src/features/session`). I dati arrivano solo dall'API same-origin;
niente token o conversazioni in localStorage.

| Percorso              | Contenuto                                                                              |
| --------------------- | -------------------------------------------------------------------------------------- |
| src/app/              | App, router (Shell, RequireSession) e layout — A-04, shell B-08.1                      |
| src/pages/            | Composizione delle feature per pagina — Identity/Login/Conversations/Settings/NotFound |
| src/features/session/ | Query identità, bootstrap, login, revoca e pannelli presentazionali — A-04, B-03.2-14  |
| src/shared/api/       | Client API comune (cookie same-origin, errori tipizzati) — creato in A-04              |
| src/shared/contracts/ | Tipi generati dai DTO backend, mai modificati a mano — creato in A-05                  |
| src/shared/ui/        | Primitive shadcn/ui possedute dal repository (ADR 0005) — B-03.3                       |
| src/shared/lib/       | Utility condivise (`cn`) — B-03.3                                                      |
| src/shared/i18n/      | Provider React it/en, lingua persistente, `html.lang` dinamico — B-03.3                |
| src/shared/styles/    | CSS globale Tailwind v4 con i token semantici mappati sulle variabili shadcn           |
| src/test/             | Setup e fixture comuni — creato in A-04                                                |
| public/               | Asset pubblici, mai documenti privati                                                  |

Navigazione primaria (B-08.1): Conversazioni (link, CRUD in B-08.2),
Documenti/Elaborati/Collegamenti (capacità future dichiarate con stato
"in corso", non cliccabili), Impostazioni (identità + lingua + logout).

## Sistema visivo: Tailwind CSS v4 + shadcn/ui (B-03.3, ADR 0005)

- Tailwind v4 CSS-first: `src/shared/styles/global.css` importa
  `tailwindcss` + `tw-animate-css`, dichiara i token semantici in `:root`
  (valori ereditati da `tokens.css`, rimosso) e li mappa con `@theme inline`.
  Plugin `@tailwindcss/vite` in `vite.config.ts`. Tema chiaro solo: il
  design attuale non ha dark mode e non viene introdotto implicitamente.
- Primitive shadcn generate con la CLI bloccata (`shadcn@4.21.0`) e
  riviste: `button`, `input`, `label`, `card`, `alert`, `skeleton` in
  `src/shared/ui` (barrel export `index.ts`); `cn` in `src/shared/lib/utils`.
  I componenti sono posseduti dal repository: niente CDN, niente
  dipendenze cloud. Le primitive specifiche Radix (`@radix-ui/react-slot`,
  `@radix-ui/react-label`) esportano il componente direttamente (non `.Root`).
- `components.json`: stile `new-york`, alias `@/shared/ui` e `@/shared/lib`.
- Regola: un nuovo bottone quando ne esistono già altri è un bug — si
  riusano le primitive; le eccezioni CSS locali vanno motivate.

## Dipendenze bloccate (21 settembre 2026)

`package.json` + `package-lock.json`; Node >= 22, installazioni sempre con
`--ignore-scripts` (NewRay.md §22.4).

| Dipendenza                            | Versione bloccata | Licenza    |
| ------------------------------------- | ----------------- | ---------- |
| react / react-dom                     | 19.3.0            | MIT        |
| react-router-dom                      | 7.18.4            | MIT        |
| @tanstack/react-query                 | 5.103.0           | MIT        |
| vite (dev)                            | 6.4.3             | MIT        |
| @vitejs/plugin-react (dev)            | 4.7.0             | MIT        |
| typescript (dev)                      | 5.8.3             | Apache-2.0 |
| @types/react, @types/react-dom (dev)  | 19.3.0            | MIT        |
| vitest (dev)                          | 3.2.7             | MIT        |
| jsdom (dev)                           | 26.1.0            | MIT        |
| @testing-library/react (dev)          | 16.3.3            | MIT        |
| @testing-library/jest-dom (dev)       | 6.9.1             | MIT        |
| @testing-library/user-event (dev)     | 14.6.7            | MIT        |
| openapi-typescript (dev)              | 7.13.0            | MIT        |
| eslint + @eslint/js (dev)             | 10.10.0 / 10.0.1  | MIT        |
| typescript-eslint (dev)               | 8.70.0            | MIT        |
| prettier (dev)                        | 3.9.7             | MIT        |
| globals (dev)                         | 17.12.0           | MIT        |
| tailwindcss + @tailwindcss/vite (dev) | 4.3.3             | MIT        |
| class-variance-authority              | 0.7.1             | Apache-2.0 |
| clsx                                  | 2.1.1             | MIT        |
| tailwind-merge                        | 3.7.0             | MIT        |
| lucide-react                          | 1.47.0            | ISC        |
| tw-animate-css                        | 1.4.0             | MIT        |
| shadcn (dev)                          | 4.21.0            | MIT        |
| @radix-ui/react-slot, react-label     | 1.3.3 / 2.1.15    | MIT        |

## Contratti generati (A-05, 16 settembre 2026)

`src/shared/contracts/api.d.ts` è generato dai DTO Pydantic del backend con
`openapi-typescript` (versione bloccata qui sopra): la rigenerazione è
`python scripts/generate_contracts.py` dal root del repository, il drift è
verificato con `--check` (vedi `contracts/README.md`). Il file non si
modifica a mano; `src/features/session/types.ts` re-esporta i tipi generati.

## Toolchain di verifica (A-06, 16 settembre 2026)

| File                                  | Responsabilità                                                            |
| ------------------------------------- | ------------------------------------------------------------------------- |
| `eslint.config.js`                    | Lint flat config (js + typescript-eslint recommended)                     |
| `.prettierrc.json`, `.prettierignore` | Formato; i contratti generati non si formattano                           |
| `scripts/check-licenses.mjs`          | Tutte le licenze del lockfile nell'allowlist (637 pacchetti)              |
| `scripts/check-bundle.mjs`            | Budget della build: JS 400 KB, CSS 64 KB (attuali ~353 / ~22.4 KB, 21/09) |

## Comandi (verificati il 16 settembre 2026)

- `npm ci --ignore-scripts` — installazione ripetibile dal lockfile
- `npm run dev` — server di sviluppo su `127.0.0.1:5173` con proxy
  `/api` → `127.0.0.1:8000` (cookie di sessione senza CORS)
- `npm run format` / `format:check` — Prettier (write / solo verifica)
- `npm run lint` — ESLint su `src/` (contratti generati esclusi)
- `npm run typecheck` — `tsc --noEmit` (strict)
- `npm run test` — Vitest + Testing Library (jsdom): comportamento locale
  degli stati della pagina (caricamento, bootstrap, conflitto 409, errore)
- `npm run licenses` — licenze dell'intero lockfile (gate: allowlist)
- `npm run build` — typecheck + bundle di produzione in `dist/`
- `npm run bundle` — budget bundle (da eseguire dopo la build)
- `npm run check` — il gate completo di NewRay.md §22.4: format, lint,
  typecheck, test, licenze, build, budget bundle

Limiti dichiarati: il test E2E nel browser (Playwright, `npm run e2e`)
è nato in B-09.1 e copre i flussi di sessione e navigazione
(`e2e/ui/session.spec.ts`, `e2e/ui/navigation.spec.ts`, 18 casi, profilo
`ui` con route API intercettate); il job CI dedicato a versioni bloccate
resta in NewRay.md §22.4. Il profilo `live` (browser → API → PostgreSQL)
è opt-in con `NEWRAY_E2E_LIVE=1`.
