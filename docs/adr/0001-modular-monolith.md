# ADR 0001 — Monolite modulare e WebUI

Data: 2026-09-16. Stato: adottato dalla specifica, implementazione da avviare.
Fonte: NewRay.md, sezioni 4, 6 e 18.

## Contesto

NewRay deve offrire gli stessi workflow nel personale e nell'ufficio, con
estensioni aggiungibili senza riscrivere chat o interfaccia.

## Decisione

Un repository ospita backend Python/FastAPI e SPA React/TypeScript/Vite.
Il backend è un monolite modulare: dominio e casi d'uso dipendono da porte;
gli adapter contengono SQL, protocolli e SDK; il bootstrap compone gli oggetti.
Inference, parser pesanti e MCP operano fuori dal ciclo HTTP.

## Conseguenze

Nessun backend applicativo parallelo in Node, framework multiagente obbligatorio
o orchestratore globale. API e worker chiamano gli stessi casi d'uso.
I controlli degli import dovranno verificare confini e cicli in CI.
