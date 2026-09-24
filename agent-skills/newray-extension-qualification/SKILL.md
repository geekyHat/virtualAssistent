---
name: newray-extension-qualification
description: Valuta e integra server MCP, modelli, skills e parser NewRay quando cambia il catalogo eseguibile o una versione di integrazione.
---

Leggi le sezioni 9–12, 17 e 20–22 di NewRay.md. Identifica origine,
release/commit, licenze, dipendenze, runtime e destinazioni di rete. Separa
capacità dichiarate da provate e componente locale da servizio cloud.

Mappa il pacchetto alla porta e al lifecycle esistenti. Specifica schema,
grant minimi, credenziali, limiti e rollback. Importazione inerte, ambiente
isolato e pin: niente codice arbitrario nel backend o branch mobili.

Verifica compatibilità, errori, stop, due principal, revoca ed egress negato.
Per modelli registra digest/hardware/contesto. Per skills valida metadata
e risorse; gli script richiedono un confine esecutivo qualificato.

Consegna scheda e prove. Se mancano account o autorizzazione ad azioni reali,
usa fake per il contratto e dichiara il limite della qualifica end-to-end.
