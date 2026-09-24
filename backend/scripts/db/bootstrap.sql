-- Bootstrap database NewRay (NewRay.md §7.3, ADR 0002).
-- Da eseguire una volta sola come superuser su un'istanza locale di
-- sviluppo (le password sono solo per sviluppo locale, mai in produzione).
--
-- Ruoli:
--   newray_migrate:   proprietario delle migrazioni e delle tabelle;
--   newray_app:       ruolo applicativo (API e worker), senza superuser né
--                     BYPASSRLS, distinto dal proprietario delle migrazioni;
--   newray_scheduler: ruolo interno NOLOGIN, mai usato per connettersi.
--                     Possiede solo le funzioni SECURITY DEFINER del
--                     worker durevole (P-05, ADR 0007): claim/heartbeat/
--                     finalize/reclaim dei run attraversano lo scope di
--                     più organizzazioni per la coda equa di sistema, cosa
--                     che newray_app non può fare restando senza BYPASSRLS.
--                     newray_migrate ne è reso membro solo per poter
--                     riassegnare la proprietà delle funzioni create dalle
--                     migrazioni (0010); non concede altrimenti privilegi.
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
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'newray_scheduler') THEN
        CREATE ROLE newray_scheduler NOLOGIN;
    END IF;
END
$$;

-- Garanzia esplicita: il ruolo applicativo non può mai eludere RLS.
ALTER ROLE newray_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

-- Ruolo interno del worker: NOLOGIN (nessuna sessione può autenticarvisi),
-- BYPASSRLS solo per le quattro funzioni SECURITY DEFINER che possiede
-- (ADR 0007). newray_migrate ne diventa membro solo per poter creare/
-- riassegnare quelle funzioni dalle migrazioni versionate. PostgreSQL
-- richiede che il NUOVO proprietario di un oggetto abbia CREATE sullo
-- schema che lo contiene (non solo chi esegue l'ALTER OWNER): questo
-- database ha lo schema public creato per ciascun database dalle
-- migrazioni stesse (0001), quindi il grant va ripetuto per ciascun
-- database applicativo, non solo qui su "postgres".
ALTER ROLE newray_scheduler NOSUPERUSER NOCREATEDB NOCREATEROLE NOLOGIN BYPASSRLS;
GRANT newray_scheduler TO newray_migrate;

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
