#!/usr/bin/env bash
# Server personale: mantiene il catalogo Ollama dell'utente corrente.
set -euo pipefail
export OLLAMA_HOST="127.0.0.1:${NEWRAY_OLLAMA_PORT:-11434}"
export OLLAMA_NO_CLOUD=1
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-8192}"
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=q8_0
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-1m}"
export OLLAMA_VULKAN=false
# Default 'amd': su questa macchina la GPU NVIDIA pilota il display, la AMD è
# l'acceleratore di calcolo. Ollama sonda ogni backend che trova sul disco e
# apre un contesto CUDA sulla scheda di display a ogni discovery della VRAM —
# all'avvio e a ogni caricamento/scaricamento di modello, non una volta sola.
# Verificato il 17 settembre 2026: né OLLAMA_LIBRARY_PATH né CUDA_VISIBLE_DEVICES
# lo impediscono, perché per il sottoprocesso di discovery Ollama imposta da sé
# CUDA_VISIBLE_DEVICES. L'unico confine che tiene è rendere il backend
# inesistente per il processo: mount namespace privato con un tmpfs vuoto sopra
# la directory cuda (nessun sudo, nessuna modifica al sistema, svanisce con il
# processo). È il contenimento descritto in NewRay.md §20.2.
case "${NEWRAY_OLLAMA_GPU:-amd}" in
  auto)
    # Sonda ogni GPU presente. Non usare quando una GPU pilota il display.
    ;;
  amd)
    if [ -z "${NEWRAY_OLLAMA_ISOLATED:-}" ]; then
      cuda_libdir=$(echo /usr/lib/ollama/cuda_v*/ | cut -d' ' -f1)
      if [ -d "$cuda_libdir" ]; then
        export NEWRAY_OLLAMA_ISOLATED=1
        exec unshare --map-root-user --mount sh -c \
          "mount -t tmpfs -o size=4k tmpfs '${cuda_libdir%/}' && exec '$0'"
      fi
    fi
    export HIP_VISIBLE_DEVICES="${HIP_VISIBLE_DEVICES:-0}"
    export ROCR_VISIBLE_DEVICES="${ROCR_VISIBLE_DEVICES:-0}"
    ;;
  cpu)
    export CUDA_VISIBLE_DEVICES=-1
    export ROCR_VISIBLE_DEVICES=-1
    ;;
  *) echo 'NEWRAY_OLLAMA_GPU deve essere auto, amd o cpu' >&2; exit 2 ;;
esac
exec ollama serve
