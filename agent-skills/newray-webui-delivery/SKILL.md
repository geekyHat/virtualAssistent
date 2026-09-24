---
name: newray-webui-delivery
description: Realizza percorsi utente NewRay in React e Vite con streaming, documenti, conferme o estensioni. Usa per cambi del comportamento visibile della WebUI.
---

Leggi le sezioni 3, 18, 19 e 22 di NewRay.md. Parti dal lavoro dell'utente,
scegli feature proprietaria e API. Non implementare autorizzazioni soltanto
nel browser.

Riusa design system e separa dati server, run e stato effimero. Progetta
caricamento, assenza dati, errore, stop e recupero pertinenti. Mantieni
modelli/MCP nell'area avanzata.

Italiano chiaro, i18n, focus e tastiera. Sanitizza contenuti esterni; logout
rimuove cache/stream privati. Le conferme mostrano azione e dati concreti.

Verifica il percorso con test proporzionati e browser quando interattivo.
Se tocchi streaming, verifica duplicati/riconnessione. Consegna risultato
utente e prove effettive, non soltanto screenshot statici.
