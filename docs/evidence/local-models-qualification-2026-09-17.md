# Qualifica dei modelli locali contro l'adapter dell'app — 17 settembre 2026

Ticket: B-03.4. Stato: **parziale, 1 modello su 4**. Non è una qualifica chiusa.

Strumento: [`scripts/qualify_local_models.py`](../../scripts/qualify_local_models.py).
A differenza di `smoke_local_models.py`, che prova caricamento e generazione sul
runtime, le sonde passano da `OllamaChatModel` e `HttpClient` del backend: quello
che passa qui è il percorso che percorrerà il run. Le capacità non ancora
implementate nell'adapter (tool call) sono provate al livello del runtime e
registrate come tali.

## Ambiente

| Voce | Valore |
| --- | --- |
| GPU di calcolo | AMD Radeon AI PRO R9700, 31,9 GiB, `gfx1201`, PCI `0000:03:00.0` |
| GPU di display | NVIDIA RTX 4060 Ti, 7,7 GiB, PCI `0000:04:00.0` — Xorg e compositor |
| Ollama | 0.34.0, loopback, `OLLAMA_MAX_LOADED_MODELS=1`, `num_ctx` 8192, KV cache q8_0 |
| Catalogo | `packs/catalogs/local-models.toml`, quattro GGUF da `/mnt/backup/llm/models` |

Un solo modello alla volta: i pesi occupano 18–20 GiB su 31,9 GiB.

## Risultato: `base` — `newray-qwen3.8-27b:ud-q5-k-m`

Digest `b424bc68bc3d84ca`, 27,3B Q5_K_M, 18,08 GiB in VRAM, contesto 8192.
Capacità dichiarate: `tools`, `thinking`, `completion`. Template di chat dal
GGUF, 9993 caratteri (non passthrough).

| Sonda | Esito | Misura |
| --- | --- | --- |
| `stream_basic` | passata | 5 delta, 85 token per «Roma, Milano, Torino» |
| `system_instruction` | passata | prefisso `[NR]` rispettato, TTFT 2,5 s |
| `multi_turn` | passata | cronologia recuperata («Verde»), TTFT 2,6 s |
| `truncation` | vedi difetto 2 | 24/24 token, **testo vuoto**, `finish_reason` `stop` |
| `cancellation` | passata | stop dopo 5 delta, runtime riutilizzabile subito |
| `thinking` | **fallita** | 201 caratteri in `message.thinking`, 11 in `content` |
| `tool_call` | passata | vedi sotto |

### Ciclo tool a due passaggi — verificato

```
turno 1 → tool_calls: knowledge_search{"query": "limite rimborso pasti trasferta"}
turno 2 → «Secondo il Manuale spese 2026, sezione 4.2, il limite di rimborso
           per i pasti in trasferta in Italia è di 46 euro al giorno.»
```

Strumento corretto, argomenti sensati in italiano, risultato del tool usato nella
risposta finale con riferimento alla fonte. **L'assunzione di NewRay.md §1** — un
modello sufficientemente capace da non richiedere un coordinatore — regge su
questo modello, con un tool singolo e uno schema semplice. Non è provata su
cataloghi ampi, tool multipli o catene lunghe.

## Difetti dell'app emersi dal vivo

Sono difetti dell'adapter, non dei modelli. Tutti e quattro dichiarano `thinking`.

1. **`message.thinking` scartato.** `OllamaChatModel` legge solo
   `message.content` e non invia `think`. Il ragionamento viene generato,
   pagato in token e latenza, e buttato via. Misurato: nella sonda
   `cancellation`, **28 secondi senza alcun delta visibile** a modello già
   caricato. Nella UI l'utente vedrebbe silenzio. Va deciso se disattivare
   `think` o se esporre il ragionamento come evento distinto.

2. **Troncamento indistinguibile da completamento.** Con `num_predict=24` il
   budget è stato consumato interamente dal ragionamento: **risposta vuota**
   presentata con `finish_reason: "stop"`. Causa: l'adapter legge
   `chunk["finish_reason"]`, ma l'API Ollama espone `done_reason`
   ([`ollama.py:155`](../../backend/src/newray/modules/models/adapters/ollama.py)),
   quindi ripiega sempre sul valore di default. Il run non può dire «troncata».
   Conferma dal vivo del rilievo R10 / ticket B-03.2-10.
   *Limite della prova:* il confronto con `done_reason` del runtime è stato
   aggiunto alla sonda dopo questa esecuzione; per `base` la diagnosi deriva dai
   dati registrati (24 token su 24, testo vuoto), non dal confronto diretto.

3. **Guasto mid-stream non mappato.** Alla morte del runtime durante una
   generazione, `httpx.RemoteProtocolError` diventa `NetworkUnreachable` e
   **sale non mappato fuori dall'adapter**: `stream()` cattura `NetworkTimeout`
   ma non `NetworkUnreachable`. Un errore di infrastruttura entra nel dominio
   invece di un codice stabile. Anch'esso in perimetro R10.

## Incidente: freeze della GPU di display

Durante l'esecuzione la sessione grafica si è bloccata e la macchina è stata
riavviata (riavvio registrato alle 00:47:59).

**Accertato.** Ollama non ha mai eseguito inferenza sulla NVIDIA: ogni
caricamento ha scelto `library=ROCm`, `--main-gpu 0`, `--split-mode none`, con
`ROCm available="31.8 GiB"`. Nessun layer sulla scheda di display, e la scheda
AMD era libera in quel momento.

**Meccanismo plausibile, non provato.** In modalità `auto` Ollama carica anche
il backend CUDA e apre un contesto sulla scheda di display a **ogni** discovery
della VRAM — all'avvio e a ogni caricamento/scaricamento. Nel run: 23
riferimenti a `cuda_v13` e 4 cicli completi in tre minuti e mezzo. Lo
scaricamento esplicito fra modelli, introdotto per liberare VRAM, moltiplica i
cicli. Non risultano Xid registrati; i log del kernel precedenti al riavvio non
sono più consultabili.

**Correzione.** `scripts/serve_ollama_local.sh`: default `amd` e isolamento del
backend CUDA tramite mount namespace privato con tmpfs vuoto sopra
`/usr/lib/ollama/cuda_v*`. Nessun sudo, nessuna modifica al sistema, svanisce
con il processo (NewRay.md §20.2).

Vie scartate perché **verificate inefficaci** il 17 settembre:

- `OLLAMA_LIBRARY_PATH` sul solo ROCm: Ollama esegue comunque un giro di
  discovery per ogni backend presente sul disco.
- `CUDA_VISIBLE_DEVICES`: per il sottoprocesso di discovery Ollama imposta da
  sé `CUDA_VISIBLE_DEVICES:0`, sovrascrivendo il valore ereditato.

Verifica dopo la correzione: `inference compute` elenca la sola R9700, zero
occorrenze di `verifying if device is supported` sulla NVIDIA.

**Precondizione aggiunta.** `qualify_local_models.py` legge la VRAM libera da
`/sys/class/drm/card*/device/mem_info_vram_*` e si ferma con exit 3 se non basta
per il modello più il margine. Con la scheda occupata il runtime può ripiegare
su un'altra GPU: meglio fermarsi con un messaggio che scoprirlo da un freeze.
Provata dal vivo a GPU occupata: exit 3, nessun modello caricato.

## Contesa di risorse non prevista

`llama-server` (llama.cpp ROCm, `/home/geekynut/src/llama.cpp/build-rocm/`) parte
al boot e occupa **27,6 GiB dei 34,2 GiB** della R9700 con Swift-Qwen3.8-27B a
`--ctx-size 131072`, servendo su `:8080`. Con quel processo attivo restano ~6,5
GiB: nessun modello del catalogo NewRay entra. Il piano risorse di NewRay.md
§8.4 assume che la GPU sia del sistema; qui il primo consumatore è un altro
servizio dell'utente.

## Da completare

`swift`, `swift-uncensored` e `gemma` non sono stati misurati. Prerequisito:
GPU AMD libera. Comando, con Ollama avviato da `scripts/serve_ollama_local.sh`:

```text
backend/.venv/bin/python scripts/qualify_local_models.py
```

I risultati di `base` restano validi ma sono stati prodotti con la sonda di
troncamento nella versione debole: una riesecuzione li renderà confrontabili
con gli altri tre.
