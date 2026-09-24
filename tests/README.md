# Verifiche trasversali

Quest'area ospiterà `e2e/` per i percorsi browser e `live/` per le prove opt-in
dei candidati reali. I test unitari, di contratto, integration e isolamento
backend saranno in `backend/tests/`; i test locali UI in `web/src/`.

Non sono presenti test applicativi in questa struttura iniziale. Il primo
caso d'uso deve portare verifiche proporzionate e comandi realmente eseguibili.

Suite ordinaria senza GPU, account esterni o rete pubblica; servizi PostgreSQL
e browser si preparano in job dedicati con versioni bloccate. Le prove live
registrano hardware, modello, corpus e configurazione, senza essere richieste
per ogni modifica ordinaria. I fake provano contratti, non la qualità AI.

I dodici contratti di accettazione in [NewRay.md §22](../NewRay.md#qualita)
guidano i test degli incrementi. Le evidenze datate vanno in `docs/evidence/`.
