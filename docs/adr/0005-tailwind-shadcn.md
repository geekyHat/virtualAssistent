# ADR 0005 — Tailwind CSS e shadcn/ui

Data: 2026-09-17. Stato: adottato su richiesta dell'utente; migrazione da fare.
Attività: B-03.3 nel backlog unico.

## Decisione

Adottare Tailwind CSS e shadcn/ui nel frontend React/TypeScript/Vite. Questa
decisione sostituisce la scelta CSS Modules per lo stile e l'assemblaggio
diretto delle primitive descritti in NewRay.md §18.1. React, routing, query
cache, reducer dei run e responsabilità delle feature restano validi.

Componenti shadcn nel codice del progetto sotto web/src/shared/ui, con sole
primitive necessarie e API comuni. Token semantici tramite CSS custom properties
e tema Tailwind; CSS globale per base e token. Eccezioni CSS locali vanno
motivate, senza conservare due implementazioni visive dello stesso componente.

## Conseguenze

Migrare prima la schermata sessione esistente. Conservare i18n, ARIA, focus,
tastiera, layout responsive e stati di errore. Bloccare dipendenze e versione
del generatore; revisionare i componenti generati e le licenze. Nessuna CDN,
servizio remoto o redesign funzionale implicito. Il ticket descrive le prove
di uscita; l'adozione non equivale a migrazione già implementata.

Riferimenti consultati per pianificare l'integrazione:
[Tailwind con Vite](https://tailwindcss.com/docs/installation/using-vite) e
[shadcn/ui per Vite esistente](https://ui.shadcn.com/docs/installation/vite).
