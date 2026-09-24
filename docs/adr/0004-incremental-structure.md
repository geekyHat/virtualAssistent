# ADR 0004 — Struttura incrementale e contratti generati

Data: 2026-09-16. Stato: adottato per la preparazione del repository.
Fonte: NewRay.md, sezioni 5, 19, 22, 23 e 24.

## Contesto

La specifica descrive il prodotto completo; l'incarico iniziale prepara la
struttura del progetto e le istruzioni degli agenti.

## Decisione

Creare le aree principali con responsabilità documentate, le istruzioni e gli
ADR. Conservare l'albero completo come mappa dei percorsi futuri. I pacchetti
Python, le feature React e le porte nascono con casi d'uso reali.

I DTO backend saranno la fonte di OpenAPI, JSON Schema e tipi TypeScript.
La generazione e il controllo del drift nasceranno con i primi endpoint.
I lockfile verranno prodotti dai package manager dopo la risoluzione delle
dipendenze; non si scrivono a mano né si dichiarano comandi ancora inesistenti.

## Conseguenze

Questa struttura non completa l'incremento A e non è un'app avviabile.
Non si creano placeholder per tutti i moduli, schemi generati fittizi,
workflow CI senza controlli reali o una licenza commerciale presunta.
AGENTS.md è autorevole; agent.md è un rimando. Il backlog è unico.
