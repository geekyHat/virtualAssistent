# ADR 0002 — Persistenza unica e scope obbligatorio

Data: 2026-09-16. Stato: adottato dalla specifica, implementazione da avviare.
Fonte: NewRay.md, sezioni 4.3, 7, 8.4 e 12.

## Contesto

Identità, condivisione, ricerca e recupero dei job devono avere un comportamento
coerente fra installazione personale e ufficio.

## Decisione

PostgreSQL è la fonte autorevole per dati, job, lease e outbox. pgvector e
full-text costituiscono la base del retrieval; la ricerca iniziale è esatta
sul corpus autorizzato. SQLAlchemy, Alembic e psycopg rimangono negli adapter.

Ogni operazione privata richiede un principal e uno scope espliciti.
ACL applicative e RLS sulle tabelle sensibili concorrono all'isolamento.
Il ruolo DB applicativo è distinto da quello delle migrazioni e non ha
superuser o BYPASSRLS. Il contesto è impostato per transazione.

## Conseguenze

Anche il personale richiede PostgreSQL; SQLite non sostituisce le prove RLS.
Non si tengono transazioni aperte durante inference o approvazioni umane.
Indici/cache sono ricostruibili e non possono ripristinare accessi revocati.
Redis, broker aggiuntivi e altri database vettoriali richiedono un bisogno misurato.
