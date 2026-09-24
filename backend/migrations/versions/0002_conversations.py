"""Seconda migrazione: conversazioni e messaggi, RLS e grant (B-01).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-16

NewRay.md §§7.2, 7.3; ADR 0002:
- proprietà esplicita: conversazione e messaggi appartengono all'utente;
- vincoli compositi impediscono riferimenti fra organizzazioni e messaggi
  su conversazioni non di proprietà dello scrivente;
- sequenza strettamente crescente per conversazione (UNIQUE);
- RLS (seconda barriera) con FORCE anche per il proprietario delle tabelle;
- il ruolo applicativo ha DML, mai CREATE.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE conversations (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES organizations (id) ON DELETE CASCADE,
            owner_id UUID NOT NULL
                REFERENCES users (id) ON DELETE CASCADE,
            title TEXT NOT NULL
                CHECK (char_length(title) BETWEEN 1 AND 200),
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            -- Coperture uniche per i FK compositi dei messaggi
            -- (stesso pattern di users_id_organization, 0001).
            CONSTRAINT conversations_id_organization UNIQUE (id, organization_id),
            CONSTRAINT conversations_id_owner UNIQUE (id, owner_id),
            CONSTRAINT conversations_owner_org
                FOREIGN KEY (owner_id, organization_id)
                REFERENCES users (id, organization_id)
        )
        """
    )
    # Lettura principale: conversazioni proprie, dalle più recenti
    # (paginazione a cursore su (updated_at, id), NewRay.md §19.2).
    op.execute(
        "CREATE INDEX conversations_owner_idx "
        "ON conversations (organization_id, owner_id, updated_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE messages (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL
                REFERENCES conversations (id) ON DELETE CASCADE,
            organization_id UUID NOT NULL,
            owner_id UUID NOT NULL,
            role TEXT NOT NULL
                CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL
                CHECK (char_length(content) BETWEEN 1 AND 50000),
            sequence INTEGER NOT NULL
                CHECK (sequence >= 1),
            created_at TIMESTAMPTZ NOT NULL,
            -- Il messaggio sta in una conversazione della stessa organizzazione...
            CONSTRAINT messages_conversation_org
                FOREIGN KEY (conversation_id, organization_id)
                REFERENCES conversations (id, organization_id),
            -- ...scritta dal proprietario di quella conversazione:
            -- barriera dello schema contro scritture cross-principal (§7.3).
            CONSTRAINT messages_conversation_owner
                FOREIGN KEY (conversation_id, owner_id)
                REFERENCES conversations (id, owner_id),
            CONSTRAINT messages_owner_org
                FOREIGN KEY (owner_id, organization_id)
                REFERENCES users (id, organization_id),
            -- Sequenza strettamente crescente per conversazione.
            CONSTRAINT messages_conversation_sequence
                UNIQUE (conversation_id, sequence)
        )
        """
    )
    op.execute(
        "CREATE INDEX messages_conversation_sequence_idx ON messages (conversation_id, sequence)"
    )

    # Seconda barriera: Row-Level Security (NewRay.md §7.3). FORCE: la
    # policy si applica anche al proprietario delle tabelle (ruolo delle
    # migrazioni), che non può eluderla.
    op.execute("ALTER TABLE conversations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE conversations FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE messages FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY conversations_isolation ON conversations
            USING (
                owner_id::text = current_setting('app.user_id', true)
                AND organization_id::text = current_setting('app.organization_id', true)
            )
        """
    )
    op.execute(
        """
        CREATE POLICY messages_isolation ON messages
            USING (
                owner_id::text = current_setting('app.user_id', true)
                AND organization_id::text = current_setting('app.organization_id', true)
            )
        """
    )

    # Il ruolo applicativo opera sui dati e mai sullo schema.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON conversations TO newray_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON messages TO newray_app")


def downgrade() -> None:
    op.execute("DROP TABLE messages")
    op.execute("DROP TABLE conversations")
