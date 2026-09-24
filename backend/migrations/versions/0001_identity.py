"""Prima migrazione: pgvector, tabelle identity, RLS e grant (A-03).

Revision ID: 0001
Revises:
Create Date: 2026-09-16

NewRay.md §§7.2, 7.3; ADR 0002:
- FK e vincoli compositi impediscono riferimenti fra organizzazioni;
- RLS (seconda barriera) su tabelle sensibili, con FORCE anche per il
  proprietario delle tabelle; contesto per transazione via current_setting;
- il ruolo applicativo ha DML, mai CREATE.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector (stack di base, NewRay.md §4.2). L'estensione è di livello
    # database e si crea come superuser in scripts/db/bootstrap.sql; qui la
    # dichiariamo se già presente (IF NOT EXISTS non richiede privilegi).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        """
        CREATE TABLE organizations (
            id UUID PRIMARY KEY,
            display_name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE users (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES organizations (id) ON DELETE RESTRICT,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL
                CHECK (role IN ('owner', 'member', 'administrator')),
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT users_id_organization UNIQUE (id, organization_id)
        )
        """
    )
    # Un solo proprietario per installazione: rende atomico il bootstrap
    # locale monouso (NewRay.md §20.3) senza letture preliminari fuori
    # scope. Con l'edizione ufficio (G) il vincolo evolve per
    # organizzazione, con migrazione esplicita.
    op.execute("CREATE UNIQUE INDEX users_single_owner ON users ((0)) WHERE role = 'owner'")
    op.execute("CREATE INDEX users_organization_idx ON users (organization_id)")

    op.execute(
        """
        CREATE TABLE sessions (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL
                REFERENCES users (id) ON DELETE CASCADE,
            organization_id UUID NOT NULL
                REFERENCES organizations (id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            revoked BOOLEAN NOT NULL DEFAULT FALSE,
            -- Vincolo composito: la sessione appartiene all'org del suo
            -- utente (NewRay.md §7.2).
            CONSTRAINT sessions_user_org
                FOREIGN KEY (user_id, organization_id)
                REFERENCES users (id, organization_id)
        )
        """
    )
    op.execute("CREATE INDEX sessions_user_idx ON sessions (user_id)")
    op.execute("CREATE INDEX sessions_expires_idx ON sessions (expires_at)")

    # Seconda barriera: Row-Level Security (NewRay.md §7.3). FORCE: la
    # policy si applica anche al proprietario delle tabelle (ruolo delle
    # migrazioni), che non può eluderla.
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sessions FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY users_isolation ON users
            USING (
                id::text = current_setting('app.user_id', true)
                AND organization_id::text = current_setting('app.organization_id', true)
            )
        """
    )
    # Le sessioni si leggono con lo scope del principal, oppure in
    # risoluzione quando è presentato l'ID opaco (app.session_lookup_id):
    # il riferimento del client viene verificato qui, non fidato (§7.1).
    op.execute(
        """
        CREATE POLICY sessions_access ON sessions
            USING (
                (user_id::text = current_setting('app.user_id', true)
                 AND organization_id::text = current_setting('app.organization_id', true))
                OR (id::text = current_setting('app.session_lookup_id', true))
            )
        """
    )

    # Il ruolo applicativo opera i dati, mai lo schema.
    op.execute("GRANT USAGE ON SCHEMA public TO newray_app")
    op.execute("GRANT SELECT, INSERT ON organizations TO newray_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON users TO newray_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON sessions TO newray_app")


def downgrade() -> None:
    op.execute("DROP TABLE sessions")
    op.execute("DROP TABLE users")
    op.execute("DROP TABLE organizations")
    # L'estensione vector resta: è di livello database (bootstrap.sql).
