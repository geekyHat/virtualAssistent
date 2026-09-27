"""P-05: ciclo di vita del worker per i run durevoli.

Aggiunge colonne di stato per claim/lease/heartbeat/deadline/cancel/
finish, permessi di UPDATE mirati e una funzione SECURITY DEFINER per il
claim cross-utente (il worker non appartiene a nessun principal e non può
usare la RLS scoped dell'app; l'aggiornamento avviene comunque solo dopo
aver posizionato le variabili `app.user_id`/`app.organization_id` del run).
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE runs
            ADD COLUMN deadline_at TIMESTAMPTZ,
            ADD COLUMN started_at TIMESTAMPTZ,
            ADD COLUMN finished_at TIMESTAMPTZ,
            ADD COLUMN cancel_requested_at TIMESTAMPTZ,
            ADD COLUMN heartbeat_at TIMESTAMPTZ,
            ADD COLUMN error_code TEXT
        """
    )
    op.execute(
        """
        CREATE INDEX runs_claim_idx
            ON runs (state, created_at)
            WHERE state IN ('queued', 'running')
        """
    )
    op.execute(
        "GRANT UPDATE (state, partial_text, finish_reason, prompt_tokens, "
        "completion_tokens, eval_duration_ns, lease_owner, lease_until, fence, "
        "updated_at, started_at, finished_at, cancel_requested_at, "
        "heartbeat_at, error_code, deadline_at) ON runs TO newray_app"
    )
    # RLS FORCE si applica anche al proprietario: aggiungo una policy
    # permissiva ristretta al ruolo delle migrazioni, unico contesto in cui
    # `runs_claim_next` (SECURITY DEFINER) gira. Il ruolo applicativo
    # `newray_app` non soddisfa questa condizione, quindi non riceve alcun
    # bypass — la sua RLS scoped resta l'unica visibile alle route pubbliche.
    op.execute(
        "CREATE POLICY runs_migrate_bypass ON runs AS PERMISSIVE FOR ALL "
        "TO PUBLIC USING (current_user = 'newray_migrate') "
        "WITH CHECK (current_user = 'newray_migrate')"
    )
    # Claim atomico cross-utente: SECURITY DEFINER lo fa girare come il
    # proprietario delle migrazioni; la policy sopra gli concede l'accesso a
    # tutte le righe. Gli aggiornamenti successivi restano sotto RLS con lo
    # scope del run.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION runs_claim_next(
            p_worker_id UUID,
            p_lease_until TIMESTAMPTZ,
            p_now TIMESTAMPTZ
        ) RETURNS SETOF runs
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_id UUID;
        BEGIN
            SELECT id INTO v_id
            FROM runs
            WHERE state = 'queued'
               OR (state = 'running' AND (lease_until IS NULL OR lease_until < p_now))
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1;

            IF v_id IS NULL THEN
                RETURN;
            END IF;

            RETURN QUERY
            UPDATE runs
               SET state = 'running',
                   lease_owner = p_worker_id,
                   lease_until = p_lease_until,
                   fence = fence + 1,
                   started_at = COALESCE(started_at, p_now),
                   heartbeat_at = p_now,
                   updated_at = p_now
             WHERE id = v_id
            RETURNING *;
        END;
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION runs_claim_next(UUID, TIMESTAMPTZ, TIMESTAMPTZ) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION runs_claim_next(UUID, TIMESTAMPTZ, TIMESTAMPTZ) TO newray_app"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS runs_claim_next(UUID, TIMESTAMPTZ, TIMESTAMPTZ)")
    op.execute("DROP POLICY IF EXISTS runs_migrate_bypass ON runs")
    op.execute("DROP INDEX IF EXISTS runs_claim_idx")
    op.execute(
        """
        ALTER TABLE runs
            DROP COLUMN deadline_at,
            DROP COLUMN started_at,
            DROP COLUMN finished_at,
            DROP COLUMN cancel_requested_at,
            DROP COLUMN heartbeat_at,
            DROP COLUMN error_code
        """
    )
