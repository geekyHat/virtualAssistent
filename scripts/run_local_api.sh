#!/usr/bin/env bash
# Avvia solo l'API. Non migra né ricrea database e non bootstrap l'owner.
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
: "${NEWRAY_DATABASE_DSN:?Impostare il DSN applicativo del database preparato}"
: "${NEWRAY_DEFAULT_MODEL_NAME:?Scegliere esplicitamente un nome da scripts/local_models.py list}"
export NEWRAY_OLLAMA_BASE_URL="${NEWRAY_OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
export NEWRAY_BIND_ADDRESS=127.0.0.1
export NEWRAY_PORT="${NEWRAY_PORT:-8000}"
# Le credenziali di migrazione non devono restare nell'ambiente dell'API.
unset NEWRAY_MIGRATION_DATABASE_URL
cd "$repo_root/backend"
exec .venv/bin/python -m newray.bootstrap.main
