# Istruzioni backend

Si applica [AGENTS.md](../AGENTS.md). Leggere NewRay.md §§4–8, 19–22 e gli
ADR pertinenti. Package, migrazioni e suite sono presenti: comandi correnti
in backend/README.md; stato ed evidenze soltanto in docs/backlog.md.

Prima del codice identificare il modulo proprietario e il suo contratto
pubblico. Dominio e applicazione usano tipi Python e porte, senza FastAPI,
Pydantic, SQLAlchemy o SDK vendor. Route e adapter traducono i confini;
il bootstrap collega le implementazioni concrete.

I repository privati richiedono scope esplicito. Non derivare il principal
dagli ID del payload. Transazioni corte; nessuna transazione aperta durante
inference, rete lenta o conferme dell'utente. Scritture e outbox sono atomiche.

Le modifiche persistenti richiedono migrazione e verifica del recupero.
Il lifespan API possiede un solo engine condiviso: i builder ricevono
l'engine, non ne creano altri. Nelle operazioni async dei profili l'intero
blocco sincrono DB viene eseguito fuori dal ciclo eventi; nessuna transazione
resta aperta durante l'attesa del catalogo di rete. Timeout DB e deadline del
run sono distinti: cancellare il consumer non interrompe un thread SQL.
Test RLS su PostgreSQL con ruolo applicativo non proprietario, due principal
e revoca; non sostituirli con fake o SQLite. Test adapter per schema, errori
e cancellazione. Suite ordinaria senza GPU, account esterni o rete pubblica.

Job con claim atomico, lease, fencing e cancellazione persistente. Gli esiti
esterni incerti vanno conservati e riconciliati. Non attribuire successo al
solo testo prodotto dal modello.

Aggiornare README con comandi realmente verificati e backlog con le evidenze.
