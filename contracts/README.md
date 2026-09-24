# Contratti generati

La fonte autorevole sono i DTO Pydantic HTTP/eventi del backend (NewRay.md
§19.1). API prodotto sotto `/api/v1/`.

Qui sta versionato l'OpenAPI generato (`openapi.json`); i tipi TypeScript
generati stanno in `web/src/shared/contracts/`. La CI verifica l'assenza di
drift tramite rigenerazione. Schemi e client generati non si modificano a
mano: si evolvono i DTO e si rigenera.

## Generazione (A-05, 16 settembre 2026)

Dal root del repository:

```sh
python scripts/generate_contracts.py            # rigenera i file
python scripts/generate_contracts.py --check    # drift → exit 1 (usato dalla CI)
```

Lo script compone l'app FastAPI con i fake dei test (niente database),
esporta `app.openapi()` in `contracts/openapi.json` (OpenAPI 3.1, rendering
deterministico: chiavi ordinate) e genera `web/src/shared/contracts/api.d.ts`
con `openapi-typescript` (versione bloccata nel lockfile di `web/`).
L'ambiente uv del backend è usato automaticamente (ri-esecuzione se
necessario).

Coperture offline senza Node: `backend/tests/contracts/test_openapi_no_drift.py`
verifica che il contratto versionato resti coerente con i DTO nella suite
ordinaria di pytest.

Limiti dichiarati: gli schemi JSON sono dentro `openapi.json`
(`components/schemas`); i file JSON Schema autonomi e le fixture di esempi
nascono con i consumatori di eventi degli incrementi B/C.
