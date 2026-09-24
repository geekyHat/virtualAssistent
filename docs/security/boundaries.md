# Confini di sicurezza da implementare

Fonte: NewRay.md §§7, 20 e 22. Questa è una mappa di requisiti, non una
certificazione o un insieme di protezioni già operative.

Il server risolve il principal. Profilo AI, ruolo account e permesso sulla
risorsa sono distinti. Scope obbligatorio su query, file, cache, chunk,
export, memoria, credenziali, job e stream, compreso replay e reconnect.

ACL applicative e RLS devono essere provate con due utenti e credenziali DB
applicative non proprietarie. L'admin gestisce account/configurazione senza
accesso ordinario ai contenuti privati; recuperi eccezionali richiedono una
procedura esplicita e audit.

Gli effetti attraversano il gateway. Conferme monouso e a scadenza sono
legate a principal e hash dell'azione; modifiche o revoche impediscono
l'effetto. Un timeout successivo a un invio produce un esito incerto da
riconciliare, non un reinvio cieco.

Importare pacchetti non esegue codice. Parser/MCP richiedono isolamento di
processo, filesystem e rete; stdio non costituisce una sandbox. Documenti,
email, pagine e risultati tool sono dati non fidati e non assegnano grant.

Il primo percorso dati deve già verificare lettura da altro principal,
revoca e cancellazione. L'esposizione ufficio richiede qualifica di
autenticazione, isolamento, TLS e recupero.
