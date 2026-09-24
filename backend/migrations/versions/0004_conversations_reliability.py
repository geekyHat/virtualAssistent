"""Quarta migrazione: scritture conversazioni affidabili (B-03.2-04, R04).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-18

NewRay.md §§7.2, 7.3, 19.2; ADR 0002:
- ``conversations.next_sequence``: prossimo valore libero per conversazione.
  Il backfill preserva le conversazioni già popolate da 0003. L'append prende
  il lock della riga padre e incrementa il contatore: la sequenza non passa
  più da una ``MAX()`` riletta (gara letture → 500 sul vincolo UNIQUE).
- ``message_appends``: ricevute di idempotenza scoped per
  (organizzazione, proprietario, conversazione, chiave). Stessa chiave e
  stesso hash del payload → lo stesso messaggio; stessa chiave e payload
  diverso → conflitto esplicito. Il vincolo UNIQUE è la barriera: il
  controllo in lettura non basta.
- RLS (FORCE) e grant minimi, stesso pattern delle tabelle 0001/0002/0003.
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Il contatore è il *prossimo* valore libero. La colonna è aggiunta
    # nullable per calcolare il backfill da messaggi creati sotto 0003;
    # solo dopo diventa NOT NULL con default per le nuove conversazioni.
    op.execute("ALTER TABLE conversations ADD COLUMN next_sequence INTEGER")
    # Le tabelle di 0002 hanno RLS con FORCE: anche il proprietario delle
    # migrazioni non vede le righe senza scope applicativo. Il backfill è una
    # manutenzione dello schema dentro questa transazione, non un caso d'uso
    # utente; disabilitiamo quindi RLS su entrambe le tabelle soltanto durante
    # la lettura/scrittura storica e ripristiniamo subito ENABLE + FORCE.
    # Serve anche ``messages``: la subquery del MAX sarebbe altrimenti vuota.
    op.execute("ALTER TABLE conversations DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE messages DISABLE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE conversations c "
        "SET next_sequence = COALESCE("
        "(SELECT MAX(m.sequence) + 1 FROM messages m WHERE m.conversation_id = c.id), 1)"
    )
    op.execute("ALTER TABLE messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE messages FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE conversations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE conversations FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE conversations ALTER COLUMN next_sequence SET DEFAULT 1")
    op.execute("ALTER TABLE conversations ALTER COLUMN next_sequence SET NOT NULL")
    op.execute(
        "ALTER TABLE conversations ADD CONSTRAINT conversations_next_sequence_positive "
        "CHECK (next_sequence >= 1)"
    )

    op.execute(
        """
        CREATE TABLE message_appends (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL
                REFERENCES conversations (id) ON DELETE CASCADE,
            organization_id UUID NOT NULL,
            owner_id UUID NOT NULL,
            idempotency_key TEXT NOT NULL
                CHECK (char_length(idempotency_key) BETWEEN 1 AND 128),
            -- SHA-256 in esadecimale del payload normalizzato (ruolo + contenuto).
            payload_hash TEXT NOT NULL
                CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            message_id UUID NOT NULL
                REFERENCES messages (id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL,
            -- Stesso scope + stessa conversazione + stessa chiave: una sola
            -- ricevuta. La ricezione non è un effetto duplicabile.
            CONSTRAINT message_appends_scope_key
                UNIQUE (organization_id, owner_id, conversation_id, idempotency_key),
            CONSTRAINT message_appends_conversation_org
                FOREIGN KEY (conversation_id, organization_id)
                REFERENCES conversations (id, organization_id),
            CONSTRAINT message_appends_conversation_owner
                FOREIGN KEY (conversation_id, owner_id)
                REFERENCES conversations (id, owner_id),
            CONSTRAINT message_appends_owner_org
                FOREIGN KEY (owner_id, organization_id)
                REFERENCES users (id, organization_id)
        )
        """
    )

    # Seconda barriera: Row-Level Security con FORCE, pattern 0002.
    op.execute("ALTER TABLE message_appends ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE message_appends FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY message_appends_isolation ON message_appends
            USING (
                owner_id::text = current_setting('app.user_id', true)
                AND organization_id::text = current_setting('app.organization_id', true)
            )
        """
    )

    # Il ruolo applicativo scrive e legge le ricevute dei propri append.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON message_appends TO newray_app")


def downgrade() -> None:
    op.execute("DROP TABLE message_appends")
    op.execute("ALTER TABLE conversations DROP COLUMN next_sequence")
