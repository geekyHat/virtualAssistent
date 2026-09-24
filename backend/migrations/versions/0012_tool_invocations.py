"""P-07: ricevute tool durevoli ed eventi del ciclo tool.

``tool_invocations`` è la ricevuta idempotente per ``(run_id, call_id)`` di
ogni chiamata tool (NewRay.md §8.3): una transizione di stato aggiorna la
riga, non ne crea un'altra. Gli stati seguono §8.3; questo pilot esercita
solo ``executing``/``succeeded``/``failed`` (tool locale di sola lettura,
sincrono). ``prepared``/``awaiting_approval``/``outcome_unknown`` restano nel
CHECK per stabilità dello schema ma non scritti: l'approval flow e la
riconciliazione degli esiti incerti valgono solo per tool con effetti
esterni (gate esplicito P-11/P-15), non esistenti nel pilot.

``newray_record_tool_event`` (SECURITY DEFINER, proprietà ``newray_scheduler``,
ADR 0007) fa l'upsert della ricevuta e appende l'evento tool nello stesso
commit fencing-checked del run ``running``: come heartbeat/finalize (0011),
"scrive solo il worker con il fence corrente". Il tipo di ``run_events`` è
esteso con ``tool.executing``/``tool.succeeded``/``tool.failed``.
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_TOOL_STATES = (
    "prepared",
    "awaiting_approval",
    "executing",
    "succeeded",
    "failed",
    "outcome_unknown",
)

_EVENT_TYPES_0011 = (
    "run.queued",
    "run.started",
    "message.delta",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "run.interrupted",
)
_EVENT_TYPES_0012 = (
    *_EVENT_TYPES_0011,
    "tool.executing",
    "tool.succeeded",
    "tool.failed",
)

_RECORD_TOOL_EVENT_FN = """
CREATE OR REPLACE FUNCTION newray_record_tool_event(
    p_run_id UUID, p_worker UUID, p_fence BIGINT, p_invocation_id UUID,
    p_call_id TEXT, p_tool_name TEXT, p_arguments JSONB, p_state TEXT,
    p_event_type TEXT, p_result TEXT, p_error_code TEXT
) RETURNS runs
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_run runs;
BEGIN
    UPDATE runs SET
        next_event_sequence = next_event_sequence + 1,
        updated_at = now()
        WHERE id = p_run_id AND lease_owner = p_worker AND fence = p_fence
            AND state = 'running'
        RETURNING * INTO v_run;
    IF v_run.id IS NULL THEN
        RETURN NULL;
    END IF;

    INSERT INTO tool_invocations (id, run_id, organization_id, owner_id, call_id,
        tool_name, arguments, state, result, error_code, created_at, updated_at)
        VALUES (p_invocation_id, v_run.id, v_run.organization_id, v_run.owner_id,
            p_call_id, p_tool_name, p_arguments, p_state, p_result, p_error_code,
            now(), now())
        ON CONFLICT (run_id, call_id) DO UPDATE SET
            state = EXCLUDED.state,
            result = EXCLUDED.result,
            error_code = EXCLUDED.error_code,
            updated_at = now();

    INSERT INTO run_events (id, run_id, organization_id, owner_id, sequence, type,
        payload, occurred_at)
        VALUES (gen_random_uuid(), v_run.id, v_run.organization_id, v_run.owner_id,
            v_run.next_event_sequence, p_event_type,
            jsonb_build_object('call_id', p_call_id, 'tool_name', p_tool_name,
                'state', p_state, 'is_error', (p_error_code IS NOT NULL)),
            now());

    RETURN v_run;
END;
$$
"""


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE tool_invocations (
            id UUID PRIMARY KEY,
            run_id UUID NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
            organization_id UUID NOT NULL,
            owner_id UUID NOT NULL,
            call_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            arguments JSONB NOT NULL,
            state TEXT NOT NULL CHECK (state IN {_TOOL_STATES!r}),
            result TEXT,
            error_code TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT tool_invocations_run_call UNIQUE (run_id, call_id),
            CONSTRAINT tool_invocations_run_org FOREIGN KEY (run_id, organization_id)
                REFERENCES runs (id, organization_id),
            CONSTRAINT tool_invocations_run_owner FOREIGN KEY (run_id, owner_id)
                REFERENCES runs (id, owner_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX tool_invocations_run_created_idx "
        "ON tool_invocations (run_id, created_at)"
    )
    op.execute("ALTER TABLE tool_invocations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tool_invocations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tool_invocations_isolation ON tool_invocations USING (
            owner_id::text = current_setting('app.user_id', true)
            AND organization_id::text = current_setting('app.organization_id', true)
        )
        """
    )
    # Il ruolo applicativo legge le proprie ricevute (GET /runs/{id}/tools);
    # non le scrive mai direttamente — solo la funzione SECURITY DEFINER,
    # fencing-checked, le upserta a nome del worker.
    op.execute("GRANT SELECT ON tool_invocations TO newray_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON tool_invocations TO newray_scheduler")

    # Estende il CHECK del tipo di run_events con i tool.* emessi (P-07).
    op.execute("ALTER TABLE run_events DROP CONSTRAINT run_events_type_check")
    op.execute(
        f"ALTER TABLE run_events ADD CONSTRAINT run_events_type_check "
        f"CHECK (type IN {_EVENT_TYPES_0012!r})"
    )

    # Il nuovo proprietario della funzione deve avere CREATE sullo schema
    # (già concesso in 0010; ripetuto è innocuo e rende la 0012 autonoma).
    op.execute("GRANT CREATE, USAGE ON SCHEMA public TO newray_scheduler")
    op.execute(_RECORD_TOOL_EVENT_FN)
    signature = (
        "newray_record_tool_event(UUID, UUID, BIGINT, UUID, TEXT, TEXT, JSONB, "
        "TEXT, TEXT, TEXT, TEXT)"
    )
    op.execute(f"ALTER FUNCTION {signature} OWNER TO newray_scheduler")
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO newray_app")


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION newray_record_tool_event("
        "UUID, UUID, BIGINT, UUID, TEXT, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT)"
    )
    op.execute("ALTER TABLE run_events DROP CONSTRAINT run_events_type_check")
    op.execute(
        f"ALTER TABLE run_events ADD CONSTRAINT run_events_type_check "
        f"CHECK (type IN {_EVENT_TYPES_0011!r})"
    )
    op.execute("DROP TABLE tool_invocations")
