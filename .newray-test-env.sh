# Ambiente di test locale (cluster pg 18.6 + pgvector su 127.0.0.1:5433).
# Solo per la sessione di sviluppo: non committare.
export NEWRAY_TEST_ADMIN_URL="postgresql+psycopg://newray@127.0.0.1:5433/postgres"
export NEWRAY_TEST_MIGRATION_URL="postgresql+psycopg://newray_migrate:newray-migrate-dev@127.0.0.1:5433/newray_test"
export NEWRAY_TEST_DATABASE_URL="postgresql+psycopg://newray_app:newray-app-dev@127.0.0.1:5433/newray_test"
