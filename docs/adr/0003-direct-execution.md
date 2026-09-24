# ADR 0003 — Esecuzione diretta del profilo selezionato

Data: 2026-09-16. Stato: adottato dalla specifica, implementazione da avviare.
Fonte: NewRay.md, sezioni 1, 8, 9, 10 e 20.

## Contesto

Il comportamento deve essere comprensibile: l'utente sceglie il profilo e
deve poter verificare fonti, strumenti impiegati ed esiti delle azioni.

## Decisione

Il run mantiene uno snapshot del profilo e del binding del modello. La
generazione e le continuazioni dopo i tool usano lo stesso modello.
Nessun Butler, classificatore di routing, critic, giudice sincrono o fallback
invisibile. Un modello mancante produce un errore recuperabile.

Gli effetti passano dal gateway e dalla policy server-side. Le approvazioni
sono legate all'azione e non ampliano i permessi; gli ACL restano revocabili
anche se il run conserva lo snapshot. Gli esiti incerti vanno riconciliati.

## Conseguenze

Ollama è il primo adapter di inference. Embedding, OCR e parser sono capacità
di supporto contabilizzate, non profili scelti automaticamente.
Il primo scheduler consente una generazione per GPU con coda limitata.
Memoria assistita e apprendimento avanzato restano opzionali e successivi.
