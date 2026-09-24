# P-03 — Gemma locale: prova nativa e limiti

Data: 23 settembre 2026. Prova eseguita sul tag locale, non su un modello
cloud. Questa scheda è evidenza collegata a [P-03](../backlog.md); non chiude
la qualifica della catena allegati/WebUI (P-10) né quella dei workflow (P-20).

## Identità dell'artefatto e ambiente

- Generativo: `gemma-4-31B-it-UD-Q4_K_XL.gguf`, SHA-256
  `9e92cb6236044c6a9870af406029c74a76e0571c157a6f95df724dcc8c7a1575`.
- Proiettore: `mmproj-F16.gguf`, SHA-256
  `6edcca228213c28d3567a35d22f849eea52d8360875093851959adf5d2f270eb`.
- MTP: `mtp-gemma-4-31B-it.gguf`, SHA-256
  `5ae8b0117bed601e8924c6305bd5b0585de361d51f0e77091bcb4252cf1f27de`;
  inventariato, **non** usato in questa prova e non sostituisce il proiettore.
- Fonte: [GGUF Unsloth, revisione del file corrispondente](https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/commit/c1ac76e99d5513b141e8adde7288b85c3f9c32ec)
  e [proiettore con SHA corrispondente](https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/blob/main/mmproj-F16.gguf).
  [Google](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/)
  e [repo Unsloth](https://huggingface.co/unsloth/gemma-4-31B-it-GGUF)
  dichiarano Apache-2.0 per Gemma 4; notice e composizione del bundle restano
  da verificare prima di distribuire pesi.
- Runtime Ollama `0.34.3`, GPU compute AMD Radeon AI PRO R9700 (ROCm gfx1201,
  34 208 743 424 byte VRAM totale). `nvidia-smi` non ha trovato un driver
  NVIDIA funzionante: nessuna prova multi-GPU/display in questa sessione.
  Il processo vLLM/Qwen nel container `radiance-r9700` occupava
  31 576 743 936 byte: con autorizzazione dell'utente è stato fermato
  (`docker stop`), senza cancellare container o dati, prima di caricare Gemma.

## Risultati riproducibili

| Caso | Tag/digest Ollama | Esito misurato |
| --- | --- | --- |
| Vecchio tag testuale | `newray-gemma4-31b-it:ud-q4-k-xl` / `cebc41a282b87037ef8e39402608819886c32abb5d7a667932161770eb2920fb` | Testo OK; `/api/show` senza `vision`; immagine sintetica rifiutata HTTP 400 (`Multimodal data provided, but model does not support multimodal requests`). Tag storico preservato. |
| Alias visivo versionato | `newray-gemma4-31b-it:ud-q4-k-xl-vision-v1` / `21528cd10ad3b6ac468f93ed5ae119bfab9c4d8adbcf5ad8c69d45f4aa95563f` | Import locale dai due GGUF con SHA verificati; `/api/show` dichiara `completion`, `vision`, `tools`, `thinking` e proiettore `clip`. |
| Sonde testo/tool alias visivo | stesso digest, `num_ctx=8192`, Ollama 0.34.3 | 7/7: stream, istruzioni, multiturno, troncamento, cancellazione, `think:false`, chiamata tool strutturata e continuazione con risultato sintetico. Nessun tool reale eseguito. |
| Visione nativa alias visivo | stesso digest, `num_ctx=8192`, due PNG 64×64 in memoria | “Rosso” e “Blu” corretti, `done=true` in entrambi; unload confermato. Caricamento Ollama: `size_vram=21 039 289 465` byte, contesto 8192. Non misura il massimo di KV/cache o concorrenza. |
| Adapter HTTP NewRay | stesso digest | 3 test live: catalogo, readiness read-only e stream testuale passati. |

Comandi eseguiti con output in `/tmp`:

```text
python3 scripts/local_models.py import --model gemma --output /tmp/newray-p03-gemma-vision-import.json
backend/.venv/bin/python scripts/qualify_local_models.py --model gemma --timeout 120 --output /tmp/newray-p03-gemma-vision-qualification.json
python3 scripts/smoke_gemma_vision.py --timeout 120 --output /tmp/newray-p03-gemma-vision-final.json
```

Il Modelfile riproducibile usa **due** `FROM`: il GGUF principale e il
proiettore. Questo è stato verificato su Ollama 0.34.3, non assunto dal solo
nome del modello. L'alias temporaneo `vision-probe` creato per la diagnosi è
stato rimosso; i due tag da conservare sono rimasti. `ollama ps` è vuoto a
fine prova e la VRAM usata è tornata a circa 0,94 GB. Il container vLLM resta
fermato e può essere riavviato dall'operatore; non è stato eliminato.

Il nome del file riporta `UD-Q4_K_XL`, mentre Ollama mostra
`quantization_level=Q4_K_M` per il GGUF: sono campi diversi e non vanno
equiparati senza ulteriore analisi. Il digest è l'identificatore operativo.

## Limiti e prossima decisione

Il nuovo default vale solo per **nuovi** Assistenti. Un Assistente già legato
al vecchio tag testuale richiede una nuova versione esplicita; nessun binding
o run storico è stato riscritto. La WebUI non invia ancora immagini all'adapter:
questa è una prova nativa del runtime, non P-10 end-to-end. Anche la chiamata
tool è nativa/sintetica: non prova autorizzazione o gateway. Il payload di
readiness è volutamente conservativo (`installed_unverified`): non registra
automaticamente una qualifica locale da file di test o da dichiarazioni vendor.

I residui originariamente aperti in P-03 sono stati trasferiti dalla decisione
del 24 settembre 2026 nel [backlog](../backlog.md): stati `available`/qualifica
locali legati a digest e hardware in P-19, fixture UI complete in P-04,
serializzazione/contesa GPU e prova multi-processo o multi-GPU nel deployment
in P-18.
Qualità su corpus, memoria massima e catena allegati sono P-20/P-10. Nessun
download o cambio automatico dei binding è stato eseguito; l'import locale
dell'alias versionato è stato invece effettuato esplicitamente.

## Verifica operativa del 24 settembre 2026

L'istanza locale non aveva un `.env` e quindi l'API non riceveva
`NEWRAY_OLLAMA_BASE_URL`: Ollama era attivo, ma la GUI lo vedeva come runtime
non configurato. Il file locale ignorato dal versionamento ora indica
`http://127.0.0.1:11434` e l'alias visivo. Il launcher ha avviato API e WebUI
su `127.0.0.1:8000` e `127.0.0.1:5173`; l'ambiente del processo API conferma
entrambe le variabili non segrete. La prova live read-only del catalogo e
della readiness sull'esatto alias/digest passa (2/2), senza caricare la GPU.
La route readiness resta autenticata: non è stato usato né creato un account
utente per verificare la vista finale nel browser. I binding storici rimangono
immutati e richiedono switch esplicito se si vuole usare l'alias con visione.

## Audit di chiusura del 24 settembre 2026

Alle 10:18 locali `rocm-smi --showpids --showmeminfo vram --json` ha rilevato
22 530 539 520 byte di VRAM usati e un processo `llama-server`; `ollama ps`
ha identificato il **vecchio tag testuale** caricato con contesto 8192. È
una situazione osservata, non un guasto da correggere fermando il processo
senza autorizzazione. Conferma però che la contesa e la selezione del binding
non sono ancora governate dal preflight richiesto da P-03. Non è stata
avviata un'altra generazione live durante questo audit.

Con i criteri originari il ticket non era chiudibile: mancavano una qualifica
persistente che decada al cambio di digest/hardware, la copertura UI di tutti
gli stati e il rilevamento operativo della contesa con processi esterni.
Su richiesta dell'utente P-03 è stato poi chiuso per il solo perimetro nativo
provato, senza dichiarare pronto il pilot: i gate mancanti hanno ora proprietari
espliciti P-04/P-18/P-19/P-20. La prova multi-GPU resta non applicabile su
questa macchina, come sopra.
