# Corpus di qualifica del pilot (P-19)

Corpus congelato per le sonde della campagna di qualifica locale
(`scripts/qualify_local_models.py`). I file qui sono materiale pubblico,
sintetico o autorizzato: **nessun documento privato, nessun prompt
proveniente da conversazioni reali, nessuna credenziale**.

Il corpus contribuisce al `corpus_hash` del rapporto della campagna: se
un file viene aggiunto, modificato o rimosso, l'hash cambia e la nuova
campagna emetterà un `run_id` nuovo. La qualifica del corpus precedente
resta valida per il suo digest, ma non copre il nuovo corpus.

## Contenuti

- `prompts/`: piccoli prompt in italiano per le sonde base (stream,
  system instruction, multi-turno, troncamento, cancellazione).
- `tools/`: descrizioni JSON degli strumenti usati nella sonda tool_call
  (`knowledge_search`).
- `LICENSE`: attribuzione e diritti d'uso del materiale.

## Soglie

Le soglie fisse della campagna (max latenza al primo delta, minimo
delta per stream, budget di troncamento, ecc.) sono in `thresholds.toml`
e vengono lette dallo script. Cambiare una soglia rende obsoleta la
qualifica precedente per contratto: il `config_hash` include il file.
