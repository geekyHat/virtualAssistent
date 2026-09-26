# Sicurezza avversariale del pilot (P-17)

Questa cartella contiene il **threat model** e la **matrice dei controlli
eseguibili** del pilot NewRay. Ogni controllo è collegato a uno o più
test di integrazione avversariale sotto
`backend/tests/integration/adversarial/`.

## Regole

- Nessun segreto, DSN, prompt integrale o percorso sensibile nei report:
  i test lo verificano ai punti di uscita.
- Fixture sintetiche, non documenti reali degli utenti (P-19 e questa
  suite condividono lo spirito: solo materiale autorizzato).
- Il ruolo applicativo dei test è `newray_app` senza `BYPASSRLS`, come
  in produzione (§7.3, ADR 0002): niente scorciatoie superutente per
  dimostrare l'assenza di un accesso.
- **Controlli negativi**: per ogni proprietà di sicurezza vitale la
  suite dimostra che il test fallirebbe se la protezione fosse disabilitata
  (bypass simulato). Questo protegge dal falso verde.
- Nessuna promessa di sicurezza assoluta: la matrice copre le superfici
  attualmente in codice (identity, conversations, profiles, runs,
  events, cancel, qualifica). Superfici future (allegati, immagini,
  RAG, skills, workflow) entreranno con la propria slice.

## Come si esegue

```
cd backend
export NEWRAY_TEST_ADMIN_URL=...  # come per la suite integration
export NEWRAY_TEST_MIGRATION_URL=...
export NEWRAY_TEST_DATABASE_URL=...
uv run pytest -q tests/integration/adversarial
```

I test dipendono da PostgreSQL 16+ con `pgvector` e i ruoli
`newray_migrate`/`newray_app` (vedi `backend/scripts/db/bootstrap.sql`).
Senza i DSN i test si saltano — non c'è modalità mock: la RLS FORCE si
verifica solo su un database reale (NewRay.md §22.2).

## Documenti

- [threat-model.md](threat-model.md) — modello di minaccia narrativo,
  attori e superfici.
- [controls.md](controls.md) — matrice dei controlli con puntatore al
  test che li esercita.
