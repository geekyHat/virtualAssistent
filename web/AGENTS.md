# Istruzioni WebUI

Si applica [AGENTS.md](../AGENTS.md). Leggere NewRay.md §§3, 18, 19 e 22.
Gli script disponibili sono in package.json; `npm run check` esegue i gate WebUI.

`app` compone provider/router; `pages` compone feature; `shared` non importa
feature. Le feature comunicano attraverso API pubbliche. Nessun secondo
backend in Node e nessun controllo autorizzativo affidato solo al browser.

Separare cache server, reducer puro del run e stato effimero. Chiavi cache
scoped per identità; logout chiude stream e svuota dati privati. Non conservare
token o conversazioni in localStorage. Rigenerare i contratti dai DTO backend.

Italiano come lingua primaria, inglese tramite le stesse chiavi i18n
(`shared/i18n`, provider React: locale persistente in `localStorage["newray.locale"]`,
`html.lang` dinamico). La scelta adottata è Tailwind CSS v4 + shadcn/ui
(ADR 0005), migrazione completata in B-03.3: i token semantici vivono in
`shared/styles/global.css` (`:root` + `@theme inline`), le primitive in
`shared/ui` (barrel `index.ts`, `cn` in `shared/lib/utils`). Riusare sempre
le primitive esistenti; un secondo sistema visivo per lo stesso componente
è un bug. Le eccezioni CSS locali vanno motivate. Testare focus, tastiera
e schermi stretti. Mostrare caricamento, assenza dati, errori e recupero
pertinenti.

Sanitizzare Markdown/contenuti esterni; niente HTML fidato del modello o
immagini remote caricate implicitamente. Anteprime e download richiedono API
autorizzate. Le conferme mostrano azione, destinatari e dati concreti.

Se cambiano gli stream verificare duplicati, buchi, snapshot e riconnessione.
Vitest/Testing Library per comportamento locale, Playwright per i percorsi
interattivi; fixture sintetiche e utenti con ruoli distinti quando pertinenti.
