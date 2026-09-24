"""Ottava migrazione: ricevute di idempotenza per il cambio modello (B-02.1).

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-23

``profile_version_switches``: ricevute scoped per (organizzazione,
proprietario, profilo, chiave). Stessa chiave e stesso hash del payload
restituisce la stessa versione senza duplicarla; stessa chiave con payload
diverso è un conflitto esplicito (stesso pattern di ``message_appends``,
migration 0004).

``profiles``/``model_bindings``/``profile_versions`` restano senza grant
UPDATE (0003): il vincolo UNIQUE ``profile_versions_profile_version`` è la
barriera di concorrenza per il cambio di modello, non un lock di riga preso
con ``SELECT ... FOR UPDATE`` (che richiederebbe il privilegio UPDATE).
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE profile_version_switches (
            id UUID PRIMARY KEY,
            profile_id UUID NOT NULL
                REFERENCES profiles (id) ON DELETE CASCADE,
            organization_id UUID NOT NULL,
            owner_id UUID NOT NULL,
            idempotency_key TEXT NOT NULL
                CHECK (char_length(idempotency_key) BETWEEN 1 AND 128),
            -- SHA-256 in esadecimale del payload normalizzato (modello +
            -- parametri + istruzioni).
            payload_hash TEXT NOT NULL
                CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            profile_version_id UUID NOT NULL
                REFERENCES profile_versions (id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL,
            -- Stesso scope + stesso profilo + stessa chiave: una sola
            -- ricevuta. La ricezione non è un effetto duplicabile.
            CONSTRAINT profile_version_switches_scope_key
                UNIQUE (organization_id, owner_id, profile_id, idempotency_key),
            CONSTRAINT profile_version_switches_profile_org
                FOREIGN KEY (profile_id, organization_id)
                REFERENCES profiles (id, organization_id),
            CONSTRAINT profile_version_switches_profile_owner
                FOREIGN KEY (profile_id, owner_id)
                REFERENCES profiles (id, owner_id),
            CONSTRAINT profile_version_switches_owner_org
                FOREIGN KEY (owner_id, organization_id)
                REFERENCES users (id, organization_id)
        )
        """
    )

    op.execute("ALTER TABLE profile_version_switches ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE profile_version_switches FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY profile_version_switches_isolation ON profile_version_switches
            USING (
                owner_id::text = current_setting('app.user_id', true)
                AND organization_id::text = current_setting('app.organization_id', true)
            )
        """
    )

    # Ricevute immutabili come le altre tabelle del modulo profiles (0003):
    # solo lettura e inserimento, mai riscritte né cancellate dal ruolo app.
    op.execute("GRANT SELECT, INSERT ON profile_version_switches TO newray_app")


def downgrade() -> None:
    op.execute("DROP TABLE profile_version_switches")
