"""P-05: run testuali persistenti, scoped e idempotenti.

Lo snapshot contiene input e binding immutabili. Il prompt non entra nella
cronologia finché la generazione non ha un esito; nessun prompt orfano.
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE runs (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
            organization_id UUID NOT NULL,
            owner_id UUID NOT NULL,
            idempotency_key TEXT NOT NULL CHECK (char_length(idempotency_key) BETWEEN 1 AND 96),
            payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            state TEXT NOT NULL CHECK (state IN (
                'queued', 'running', 'waiting_approval', 'completed',
                'failed', 'cancelled', 'interrupted'
            )),
            snapshot JSONB NOT NULL,
            partial_text TEXT NOT NULL DEFAULT '',
            finish_reason TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            eval_duration_ns BIGINT,
            lease_owner UUID,
            lease_until TIMESTAMPTZ,
            fence BIGINT NOT NULL DEFAULT 0 CHECK (fence >= 0),
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT runs_scope_key UNIQUE (
                organization_id, owner_id, conversation_id, idempotency_key
            ),
            CONSTRAINT runs_conversation_org FOREIGN KEY (conversation_id, organization_id)
                REFERENCES conversations (id, organization_id),
            CONSTRAINT runs_conversation_owner FOREIGN KEY (conversation_id, owner_id)
                REFERENCES conversations (id, owner_id),
            CONSTRAINT runs_owner_org FOREIGN KEY (owner_id, organization_id)
                REFERENCES users (id, organization_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX runs_owner_state_idx ON runs (organization_id, owner_id, state, created_at)"
    )
    op.execute("ALTER TABLE runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE runs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY runs_isolation ON runs USING (
            owner_id::text = current_setting('app.user_id', true)
            AND organization_id::text = current_setting('app.organization_id', true)
        )
        """
    )
    # Snapshot, scope e ricevuta non sono modificabili dal ruolo applicativo.
    op.execute("GRANT SELECT, INSERT ON runs TO newray_app")
    op.execute(
        "GRANT UPDATE (state, partial_text, finish_reason, prompt_tokens, "
        "completion_tokens, eval_duration_ns, lease_owner, lease_until, fence, updated_at) "
        "ON runs TO newray_app"
    )


def downgrade() -> None:
    op.execute("DROP TABLE runs")
