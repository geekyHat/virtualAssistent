#!/bin/sh
# Avvio API NewRay (A-07): prima le migrazioni con il ruolo proprietario
# (NEWRAY_MIGRATION_DATABASE_URL, ADR 0002), poi uvicorn con i settings
# validati (bind loopback, porta, dati — NewRay.md §21.2).
set -eu

cd /opt/newray/backend
alembic upgrade head
exec python -m newray.bootstrap.main
