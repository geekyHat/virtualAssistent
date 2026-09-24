"""Credenziali locali del proprietario e policy pre-auth (B-03.2-14).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-20

Aggiunge il record hash della credenziale sul singolo owner locale
(NewRay.md §7.2, §20.3) e la policy RLS che autorizza il lookup
pre-autenticato usato dal login e dallo status pubblico
dell'installazione. Le policy esistenti non vengono toccate: la nuova è
strettamente restrittiva a ``role = 'owner'`` e attiva solo quando il
flag di lookup della transazione corrente è impostato — le route
autenticate non lo abilitano mai per errore. Nessun bypass RLS globale,
nessun default ambiguo per gli owner esistenti (colonna nullable →
mappata dal caso d'uso in ``CREDENTIAL_NOT_SET``, non in
``INVALID_CREDENTIALS``).
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN credential_hash TEXT")

    # Lookup pre-auth: la SELECT è ammessa se il flag di transazione è
    # attivo e la riga è quella dell'unico owner. La policy usa USING
    # senza WITH CHECK: nessuna nuova facoltà di scrittura, solo lettura.
    op.execute(
        """
        CREATE POLICY users_owner_lookup ON users
            FOR SELECT
            USING (
                role = 'owner'
                AND current_setting('app.owner_lookup', true) = 'true'
            )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY users_owner_lookup ON users")
    op.execute("ALTER TABLE users DROP COLUMN credential_hash")
