#!/bin/sh
# Prima inizializzazione del volume DB (A-07, ADR 0002): crea il ruolo
# applicativo — distinto dal proprietario delle migrazioni, che è
# POSTGRES_USER — e l'estensione pgvector nel database newray.
#
# Corre una sola volta, con il volume vuoto, come superuser. La password
# arriva dall'ambiente (valore casuali esadecimale, senza apici).
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE newray_app LOGIN PASSWORD '${NEWRAY_DB_APP_PASSWORD}'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE EXTENSION IF NOT EXISTS vector;
SQL
