"""P-06: eventi durevoli e cancellazione dei run.

Aggiunge:

- ``runs.cancel_requested_at`` (timestamp nullable) per la cancellazione
  persistita e idempotente. Solo il ruolo applicativo può scriverla
  attraverso i canali di identità dell'owner.
- Tabella ``run_events`` (outbox) con ``sequence`` monotona per run,
  ``event_type`` e ``payload JSONB``. Ogni scrittura di partial o di
  esito su ``runs`` produce un evento nella stessa transazione:
  nessun evento orfano, nessuno stato senza evento.
- RLS FORCE su ``run_events`` con la stessa doppia policy dei run:
  isolamento per principal utente + accesso worker via ``app.worker_id``.

L'FK dagli eventi al run è single-column (``run_id``): l'integrità dello
scope è garantita dal fatto che l'INSERT parte dalla stessa transazione
che verifica il claim/lease del run e imposta ``organization_id``/
``owner_id`` coerenti (il ruolo applicativo non li aggiorna in seguito
— non è nemmeno concesso UPDATE su ``run_events``).
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE runs ADD COLUMN cancel_requested_at TIMESTAMPTZ")
    op.execute("GRANT UPDATE (cancel_requested_at) ON runs TO newray_app")
    op.execute(
        """
        CREATE TABLE run_events (
            id UUID PRIMARY KEY,
            run_id UUID NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
            organization_id UUID NOT NULL,
            owner_id UUID NOT NULL,
            sequence BIGINT NOT NULL CHECK (sequence >= 1),
            event_type TEXT NOT NULL CHECK (event_type IN (
                'delta', 'partial', 'completed', 'failed',
                'cancelled', 'interrupted', 'cancel_requested'
            )),
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT run_events_run_sequence UNIQUE (run_id, sequence)
        )
        """
    )
    op.execute("CREATE INDEX run_events_run_sequence_idx ON run_events (run_id, sequence)")
    op.execute("ALTER TABLE run_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE run_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY run_events_isolation ON run_events USING (
            owner_id::text = current_setting('app.user_id', true)
            AND organization_id::text = current_setting('app.organization_id', true)
        )
        """
    )
    op.execute(
        """
        CREATE POLICY run_events_worker_access ON run_events
            FOR ALL
            USING (current_setting('app.worker_id', true) <> '')
            WITH CHECK (current_setting('app.worker_id', true) <> '')
        """
    )
    # Gli eventi sono immutabili: solo SELECT/INSERT per il ruolo
    # applicativo. Nessun UPDATE/DELETE ammesso: la cancellazione
    # dell'intera catena avviene solo via CASCADE sulla runs.
    op.execute("GRANT SELECT, INSERT ON run_events TO newray_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS run_events")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS cancel_requested_at")
