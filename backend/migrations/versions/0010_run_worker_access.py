"""P-05: accesso worker allo scheduler dei run.

Il consumo della coda richiede visibilità cross-scope su ``runs``. La
policy `runs_worker_access` la concede solo quando il chiamante ha
dichiarato l'identità di worker nel session variable ``app.worker_id``.
Il set della variabile è responsabilità del processo di scheduler,
mai delle route utente: le route non toccano ``app.worker_id`` e la
policy di isolamento continua a proteggere l'accesso per principal.

Il valore di ``app.worker_id`` è opaco (una UUID); qui verifichiamo solo
che sia non vuoto, senza permessi impliciti derivati dal valore stesso.
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE POLICY runs_worker_access ON runs
            FOR ALL
            USING (current_setting('app.worker_id', true) <> '')
            WITH CHECK (current_setting('app.worker_id', true) <> '')
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS runs_worker_access ON runs")
