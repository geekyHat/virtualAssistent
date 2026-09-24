"""Terza migrazione: profili, versioni e binding dei modelli (B-02).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-16

NewRay.md §§7.2, 7.3, 9.1; ADR 0002:
- entità di configurazione a versioni immutabili usate dagli snapshot
  (profile, profile_version, model_binding);
- un profilo per tipo e proprietario (seeding idempotente sicuro);
- vincoli compositi impediscono riferimenti fra organizzazioni e versioni
  su profili non di proprietà dello scrivente;
- RLS (seconda barriera) con FORCE anche per il proprietario delle
  tabelle;
- il ruolo applicativo ha solo SELECT/INSERT: nessuna modifica in-place
  di versioni o binding, a livello di privilegio.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE profiles (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES organizations (id) ON DELETE CASCADE,
            owner_id UUID NOT NULL
                REFERENCES users (id) ON DELETE CASCADE,
            kind TEXT NOT NULL
                CHECK (kind IN ('assistant', 'researcher', 'coder')),
            display_name TEXT NOT NULL
                CHECK (char_length(display_name) BETWEEN 1 AND 100),
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            -- Coperture uniche per i FK compositi delle versioni
            -- (stesso pattern di conversations_id_organization, 0002).
            CONSTRAINT profiles_id_organization UNIQUE (id, organization_id),
            CONSTRAINT profiles_id_owner UNIQUE (id, owner_id),
            -- Un profilo per tipo per proprietario: rende il seeding
            -- dei default sicuro anche in caso di concorrenza.
            CONSTRAINT profiles_owner_kind UNIQUE (organization_id, owner_id, kind),
            CONSTRAINT profiles_owner_org
                FOREIGN KEY (owner_id, organization_id)
                REFERENCES users (id, organization_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE model_bindings (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES organizations (id) ON DELETE CASCADE,
            owner_id UUID NOT NULL
                REFERENCES users (id) ON DELETE CASCADE,
            name TEXT NOT NULL
                CHECK (char_length(name) BETWEEN 1 AND 100),
            runtime TEXT NOT NULL CHECK (runtime = 'ollama'),
            model_name TEXT NOT NULL
                CHECK (char_length(model_name) BETWEEN 1 AND 200),
            parameters JSONB NOT NULL DEFAULT '{}'::jsonb
                CHECK (jsonb_typeof(parameters) = 'object'),
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT model_bindings_id_organization UNIQUE (id, organization_id),
            CONSTRAINT model_bindings_id_owner UNIQUE (id, owner_id),
            -- Il riferimento del binding è per proprietario.
            CONSTRAINT model_bindings_owner_name UNIQUE (organization_id, owner_id, name),
            CONSTRAINT model_bindings_owner_org
                FOREIGN KEY (owner_id, organization_id)
                REFERENCES users (id, organization_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE profile_versions (
            id UUID PRIMARY KEY,
            profile_id UUID NOT NULL,
            organization_id UUID NOT NULL
                REFERENCES organizations (id) ON DELETE CASCADE,
            owner_id UUID NOT NULL
                REFERENCES users (id) ON DELETE CASCADE,
            version TEXT NOT NULL
                CHECK (char_length(version) BETWEEN 1 AND 40),
            model_binding_id UUID NOT NULL,
            instructions TEXT NOT NULL DEFAULT ''
                CHECK (char_length(instructions) <= 20000),
            created_at TIMESTAMPTZ NOT NULL,
            -- La versione sta in un profilo della stessa organizzazione...
            CONSTRAINT profile_versions_profile_org
                FOREIGN KEY (profile_id, organization_id)
                REFERENCES profiles (id, organization_id)
                ON DELETE CASCADE,
            -- ...scritta dal proprietario di quel profilo: barriera dello
            -- schema contro scritture cross-principal (§7.3).
            CONSTRAINT profile_versions_profile_owner
                FOREIGN KEY (profile_id, owner_id)
                REFERENCES profiles (id, owner_id)
                ON DELETE CASCADE,
            -- ...e il binding è della stessa organizzazione; senza
            -- ON DELETE resta REFERENCED: un binding referenziato da una
            -- versione non può essere eliminato (versioni immutabili).
            CONSTRAINT profile_versions_binding_org
                FOREIGN KEY (model_binding_id, organization_id)
                REFERENCES model_bindings (id, organization_id),
            CONSTRAINT profile_versions_binding_owner
                FOREIGN KEY (model_binding_id, owner_id)
                REFERENCES model_bindings (id, owner_id),
            CONSTRAINT profile_versions_owner_org
                FOREIGN KEY (owner_id, organization_id)
                REFERENCES users (id, organization_id),
            CONSTRAINT profile_versions_profile_version
                UNIQUE (profile_id, version)
        )
        """
    )
    op.execute(
        "CREATE INDEX profile_versions_profile_idx ON profile_versions (profile_id, created_at)"
    )

    # Seconda barriera: Row-Level Security (NewRay.md §7.3). FORCE: la
    # policy si applica anche al proprietario delle tabelle (ruolo delle
    # migrazioni), che non può eluderla.
    for table in ("profiles", "model_bindings", "profile_versions"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    for table in ("profiles", "model_bindings", "profile_versions"):
        op.execute(
            f"""
            CREATE POLICY {table}_isolation ON {table}
                USING (
                    owner_id::text = current_setting('app.user_id', true)
                    AND organization_id::text = current_setting('app.organization_id', true)
                )
            """
        )

    # Il ruolo applicativo legge e crea (seeding), ma non modifica né
    # cancella: versioni e binding sono immutabili anche a livello di
    # privilegio (NewRay.md §7.2).
    for table in ("profiles", "model_bindings", "profile_versions"):
        op.execute(f"GRANT SELECT, INSERT ON {table} TO newray_app")


def downgrade() -> None:
    op.execute("DROP TABLE profile_versions")
    op.execute("DROP TABLE model_bindings")
    op.execute("DROP TABLE profiles")
