-- Bootstrap database NewRay (NewRay.md §7.3, ADR 0002).
-- Da eseguire una volta sola come superuser su un'istanza locale di
-- sviluppo (le password sono solo per sviluppo locale, mai in produzione).
--
-- Ruoli:
--   newray_migrate: proprietario delle migrazioni e delle tabelle;
--   newray_app:     ruolo applicativo, senza superuser né BYPASSRLS,
--                   distinto dal proprietario delle migrazioni.
--
-- L'estensione pgvector è di livello database e si crea qui, come superuser.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'newray_migrate') THEN
        CREATE ROLE newray_migrate LOGIN PASSWORD 'newray-migrate-dev';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'newray_app') THEN
        CREATE ROLE newray_app LOGIN PASSWORD 'newray-app-dev';
    END IF;
END
$$;

-- Garanzia esplicita: il ruolo applicativo non può mai eludere RLS.
ALTER ROLE newray_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

-- Database: "newray" per l'uso, "newray_test" come base dei test di
-- integrazione (ogni test crea il proprio database dalla base).
SELECT 'CREATE DATABASE newray OWNER newray_migrate'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'newray')\gexec

SELECT 'CREATE DATABASE newray_test OWNER newray_migrate'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'newray_test')\gexec

-- Estensione pgvector in entrambi i database (operazione di livello
-- database: richiede il superuser).
\c newray
CREATE EXTENSION IF NOT EXISTS vector;

\c newray_test
CREATE EXTENSION IF NOT EXISTS vector;
