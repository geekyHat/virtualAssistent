"""Scope organizations e separazione lookup/revoca sessioni (B-03.2-22).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-22

``organizations`` diventa una superficie privata con RLS/ FORCE. Le policy
di ``sessions`` separano la risoluzione pre-auth per ID opaco dalla mutazione:
presentare l'ID di una sessione permette di verificarla, non di revocarla.
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY organizations_scope ON organizations
            USING (
                id::text = current_setting('app.organization_id', true)
            )
            WITH CHECK (
                id::text = current_setting('app.organization_id', true)
            )
        """
    )

    op.execute("DROP POLICY sessions_access ON sessions")
    op.execute(
        """
        CREATE POLICY sessions_select ON sessions
            FOR SELECT
            USING (
                (user_id::text = current_setting('app.user_id', true)
                 AND organization_id::text = current_setting('app.organization_id', true))
                OR (id::text = current_setting('app.session_lookup_id', true))
            )
        """
    )
    op.execute(
        """
        CREATE POLICY sessions_insert ON sessions
            FOR INSERT
            WITH CHECK (
                user_id::text = current_setting('app.user_id', true)
                AND organization_id::text = current_setting('app.organization_id', true)
            )
        """
    )
    op.execute(
        """
        CREATE POLICY sessions_update ON sessions
            FOR UPDATE
            USING (
                user_id::text = current_setting('app.user_id', true)
                AND organization_id::text = current_setting('app.organization_id', true)
            )
            WITH CHECK (
                user_id::text = current_setting('app.user_id', true)
                AND organization_id::text = current_setting('app.organization_id', true)
            )
        """
    )
    op.execute(
        """
        CREATE POLICY sessions_delete ON sessions
            FOR DELETE
            USING (
                user_id::text = current_setting('app.user_id', true)
                AND organization_id::text = current_setting('app.organization_id', true)
            )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY sessions_delete ON sessions")
    op.execute("DROP POLICY sessions_update ON sessions")
    op.execute("DROP POLICY sessions_insert ON sessions")
    op.execute("DROP POLICY sessions_select ON sessions")
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
    op.execute("DROP POLICY organizations_scope ON organizations")
    op.execute("ALTER TABLE organizations NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations DISABLE ROW LEVEL SECURITY")
