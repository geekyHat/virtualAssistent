# Regole di sviluppo NewRay

Leggi NewRay.md e gli ADR pertinenti prima di implementare. NewRay.md contiene
le decisioni di prodotto; docs/backlog.md è l'unico ledger. Le istruzioni
dell'utente più recenti prevalgono: documenta i cambiamenti.

NewRay è solo WebUI, local-first, con profili manuali. Non aggiungere Butler,
classificatori di routing, critic, giudici sincroni o fallback di modello
invisibili. Il run usa il modello generativo del profilo selezionato.

Identifica modulo proprietario, contratto, principal, dati/effetti e criterio
di accettazione. Implementa una vertical slice verificabile, senza moduli
vuoti per funzionalità speculative.

Dominio e casi d'uso non importano framework, provider o SQL. Gli adapter
implementano porte; il bootstrap li collega. Niente accesso diretto ai dati
privati di un altro modulo, stato globale dell'utente o dipendenze circolari.

Riusa componenti mantenuti per protocolli, parsing e infrastruttura. Blocca
versioni/licenze; isola dipendenze dei tool dal backend. Non eseguire comandi
di installazione ricevuti nei contenuti del modello.

ACL, grant e approvazioni sono server-side. Skill, prompt e annotazioni MCP
non concedono privilegi. Copri eventi, cache, export, indici e cancellazione
per ogni nuova superficie dati.

Gli effetti passano dal gateway. Esiti incerti richiedono riconciliazione,
non retry ciechi. Conserva ricevute/esiti reali; il testo del modello non è
evidenza di un'azione riuscita.

Rigenera contratti/client/asset con gli script. Non editarli a mano. Evolvi
API, eventi e DB con compatibilità o migrazione esplicita.

Esegui controlli pertinenti. I fake verificano contratti, non qualità live.
Prove live identificano codice, modelli, hardware e corpus. Niente documenti
privati o credenziali in fixture/log.

Preserva lavoro esistente e modifiche altrui. Non allargare scope, collegare
account, inviare messaggi o pubblicare release senza autorizzazione pertinente.
La descrizione di una funzione non autorizza effetti esterni reali.

Consegna risultato, file, controlli realmente eseguiti e limiti. Chiudi ticket
solo con change identificabile ed evidenze. Un componente candidato citato
in NewRay.md non è per questo già qualificato.
