"""P-06: eventi durevoli del run e cancellazione persistita.

``run_events`` è il log/outbox degli eventi (NewRay.md §19.3, §7.4): ogni
transizione fencing-checked ne appende esattamente uno nella stessa
transazione, quindi "terminale unico" eredita la stessa barriera già
provata in P-05 (0010), non richiede logica applicativa separata. Le
funzioni SECURITY DEFINER di 0010 sono ``CREATE OR REPLACE`` con lo stesso
proprietario/firma: nessun nuovo attraversamento di scope oltre a quello
già motivato in ADR 0007.

``run.waiting_approval``/``tool.*``/``sources.updated``/``artifact.updated``
di §19.3 non sono emessi in questo pilot: nessun tool prima di P-07,
nessuna fonte prima di P-11 (dichiarato, non taciuto).

La cancellazione di un run ``queued`` è una transizione diretta (nessun
worker lo possiede ancora, nessun fencing necessario) fatta dal ruolo
applicativo nel proprio scope; solo la propagazione a un run ``running``
passa dal worker (``cancel_requested_at``, letto dopo l'heartbeat).

Il payload di ``message.delta`` è il testo CUMULATIVO noto al checkpoint,
non un frammento incrementale: rende il reducer del client idempotente per
costruzione (riapplicare due volte lo stesso evento è un no-op reale), a
costo di ritrasmettere il testo già inviato — accettabile per il budget di
output del pilot (§8.2, poche migliaia di token).
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_EVENT_TYPES = (
    "run.queued",
    "run.started",
    "message.delta",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "run.interrupted",
)

#: Retention dei soli eventi ``message.delta`` (non di ciclo vita, al più 5
#: per run): valore iniziale dichiarato (NewRay.md §19.3 avvisa contro
#: buffer illimitati nel producer, non fissa un numero), da tarare in
#: P-19/P-20. Dà un significato reale a "cursore scaduto" → ``resync``.
MAX_RETAINED_DELTA_EVENTS = 50


def upgrade() -> None:
    op.execute("ALTER TABLE runs ADD COLUMN next_event_sequence BIGINT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE runs ADD COLUMN cancel_requested_at TIMESTAMPTZ")
    # newray_app scrive cancel_requested_at (richiesta di cancel, proprio
    # scope) e deve poter includere next_event_sequence/state/finish_reason
    # nello stesso UPDATE quando un run 'queued' transita subito a
    # 'cancelled' (nessun worker coinvolto, non serve una funzione
    # SECURITY DEFINER): PostgreSQL richiede il grant su OGNI colonna nel
    # SET, anche quella che una riga particolare non finisce per cambiare.
    op.execute("GRANT UPDATE (cancel_requested_at, next_event_sequence) ON runs TO newray_app")
    op.execute("GRANT UPDATE (next_event_sequence) ON runs TO newray_scheduler")

    # ``run_events`` referenzia (id, organization_id)/(id, owner_id) di
    # ``runs``: le UNIQUE composite devono esistere PRIMA della FK che le
    # referenzia più sotto (stesso ruolo di quelle già presenti su
    # conversations/users per i vincoli di runs stessa, migrazione 0009).
    op.execute(
        "ALTER TABLE runs ADD CONSTRAINT runs_id_organization UNIQUE (id, organization_id)"
    )
    op.execute("ALTER TABLE runs ADD CONSTRAINT runs_id_owner UNIQUE (id, owner_id)")

    op.execute(
        f"""
        CREATE TABLE run_events (
            id UUID PRIMARY KEY,
            run_id UUID NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
            organization_id UUID NOT NULL,
            owner_id UUID NOT NULL,
            sequence BIGINT NOT NULL CHECK (sequence >= 1),
            type TEXT NOT NULL CHECK (type IN {_EVENT_TYPES!r}),
            payload JSONB NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT run_events_run_sequence UNIQUE (run_id, sequence),
            CONSTRAINT run_events_run_org FOREIGN KEY (run_id, organization_id)
                REFERENCES runs (id, organization_id),
            CONSTRAINT run_events_run_owner FOREIGN KEY (run_id, owner_id)
                REFERENCES runs (id, owner_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX run_events_run_sequence_idx ON run_events (run_id, sequence)"
    )
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
    # Il ruolo applicativo appende solo i propri eventi di scope (run.queued
    # in enqueue, run.cancelled quando cancella un run ancora queued); mai
    # UPDATE/DELETE, un evento durevole non si riscrive.
    op.execute("GRANT SELECT, INSERT ON run_events TO newray_app")
    # Le funzioni SECURITY DEFINER appendono cross-org (claim/heartbeat/
    # finalize/reclaim) e potano i soli message.delta in eccesso.
    op.execute("GRANT SELECT, INSERT, DELETE ON run_events TO newray_scheduler")

    # -- claim: come 0010, più append di run.started nella stessa transazione. --
    op.execute(
        """
        CREATE OR REPLACE FUNCTION newray_claim_run(
            p_worker UUID, p_lease_seconds INTEGER, p_resource TEXT
        ) RETURNS runs
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            v_run runs;
            v_lease run_resource_leases;
        BEGIN
            SELECT * INTO v_lease FROM run_resource_leases
                WHERE resource_id = p_resource FOR UPDATE;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            IF v_lease.lease_until IS NOT NULL AND v_lease.lease_until > now()
                AND v_lease.owner_worker_id IS DISTINCT FROM p_worker THEN
                RETURN NULL;
            END IF;

            SELECT * INTO v_run FROM runs WHERE state = 'queued'
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1;
            IF v_run.id IS NULL THEN
                RETURN NULL;
            END IF;

            UPDATE run_resource_leases SET
                owner_worker_id = p_worker,
                owner_run_id = v_run.id,
                lease_until = now() + make_interval(secs => p_lease_seconds),
                fence = fence + 1,
                updated_at = now()
                WHERE resource_id = p_resource;

            UPDATE runs SET
                state = 'running',
                lease_owner = p_worker,
                lease_until = now() + make_interval(secs => p_lease_seconds),
                fence = fence + 1,
                next_event_sequence = next_event_sequence + 1,
                updated_at = now()
                WHERE id = v_run.id
                RETURNING * INTO v_run;

            INSERT INTO run_events (id, run_id, organization_id, owner_id, sequence, type,
                payload, occurred_at)
                VALUES (gen_random_uuid(), v_run.id, v_run.organization_id, v_run.owner_id,
                    v_run.next_event_sequence, 'run.started', '{}'::jsonb, now());

            RETURN v_run;
        END;
        $$
        """
    )

    # -- heartbeat: come 0010, più message.delta (testo cumulativo) e
    # retention dei delta oltre MAX_RETAINED_DELTA_EVENTS. --
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION newray_heartbeat_run(
            p_run_id UUID, p_worker UUID, p_fence BIGINT, p_lease_seconds INTEGER,
            p_partial_text TEXT, p_resource TEXT
        ) RETURNS runs
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            v_run runs;
        BEGIN
            UPDATE runs SET
                partial_text = p_partial_text,
                lease_until = now() + make_interval(secs => p_lease_seconds),
                next_event_sequence = next_event_sequence + 1,
                updated_at = now()
                WHERE id = p_run_id AND lease_owner = p_worker AND fence = p_fence
                    AND state = 'running'
                RETURNING * INTO v_run;
            IF v_run.id IS NULL THEN
                RETURN NULL;
            END IF;

            INSERT INTO run_events (id, run_id, organization_id, owner_id, sequence, type,
                payload, occurred_at)
                VALUES (gen_random_uuid(), v_run.id, v_run.organization_id, v_run.owner_id,
                    v_run.next_event_sequence, 'message.delta',
                    jsonb_build_object('text', p_partial_text), now());

            DELETE FROM run_events
                WHERE id IN (
                    SELECT id FROM run_events
                        WHERE run_id = p_run_id AND type = 'message.delta'
                        ORDER BY sequence DESC
                        OFFSET {MAX_RETAINED_DELTA_EVENTS}
                );

            UPDATE run_resource_leases SET
                lease_until = now() + make_interval(secs => p_lease_seconds),
                updated_at = now()
                WHERE resource_id = p_resource AND owner_worker_id = p_worker
                    AND owner_run_id = p_run_id;

            RETURN v_run;
        END;
        $$
        """
    )

    # -- finalize: come 0010, più append dell'evento terminale
    # (run.completed/failed/cancelled a seconda di p_state). --
    op.execute(
        """
        CREATE OR REPLACE FUNCTION newray_finalize_run(
            p_run_id UUID, p_worker UUID, p_fence BIGINT, p_state TEXT,
            p_finish_reason TEXT, p_prompt_tokens INTEGER, p_completion_tokens INTEGER,
            p_eval_duration_ns BIGINT, p_partial_text TEXT, p_resource TEXT
        ) RETURNS runs
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            v_run runs;
        BEGIN
            UPDATE runs SET
                state = p_state,
                finish_reason = p_finish_reason,
                prompt_tokens = p_prompt_tokens,
                completion_tokens = p_completion_tokens,
                eval_duration_ns = p_eval_duration_ns,
                partial_text = p_partial_text,
                next_event_sequence = next_event_sequence + 1,
                updated_at = now()
                WHERE id = p_run_id AND lease_owner = p_worker AND fence = p_fence
                    AND state = 'running'
                RETURNING * INTO v_run;
            IF v_run.id IS NULL THEN
                RETURN NULL;
            END IF;

            INSERT INTO run_events (id, run_id, organization_id, owner_id, sequence, type,
                payload, occurred_at)
                VALUES (gen_random_uuid(), v_run.id, v_run.organization_id, v_run.owner_id,
                    v_run.next_event_sequence, 'run.' || p_state,
                    jsonb_build_object('finish_reason', p_finish_reason, 'text', p_partial_text,
                        'prompt_tokens', p_prompt_tokens, 'completion_tokens',
                        p_completion_tokens, 'eval_duration_ns', p_eval_duration_ns),
                    now());

            UPDATE run_resource_leases SET
                owner_run_id = NULL, owner_worker_id = NULL, lease_until = NULL,
                updated_at = now()
                WHERE resource_id = p_resource AND owner_worker_id = p_worker
                    AND owner_run_id = p_run_id;

            RETURN v_run;
        END;
        $$
        """
    )

    # -- reclaim: come 0010, più append di run.interrupted. --
    op.execute(
        """
        CREATE OR REPLACE FUNCTION newray_reclaim_stale_runs(p_grace_seconds INTEGER)
        RETURNS SETOF runs
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            v_run runs;
        BEGIN
            FOR v_run IN
                SELECT r.* FROM runs r
                    WHERE r.state = 'running' AND r.lease_until < now()
                    AND NOT EXISTS (
                        SELECT 1 FROM run_workers w
                            WHERE w.worker_id = r.lease_owner
                            AND w.last_heartbeat_at > now() - make_interval(secs => p_grace_seconds)
                    )
                    FOR UPDATE SKIP LOCKED
            LOOP
                UPDATE runs SET
                    state = 'interrupted',
                    finish_reason = 'worker_lost',
                    next_event_sequence = next_event_sequence + 1,
                    updated_at = now()
                    WHERE id = v_run.id AND lease_owner = v_run.lease_owner
                        AND fence = v_run.fence
                    RETURNING * INTO v_run;
                IF v_run.id IS NOT NULL THEN
                    INSERT INTO run_events (id, run_id, organization_id, owner_id, sequence,
                        type, payload, occurred_at)
                        VALUES (gen_random_uuid(), v_run.id, v_run.organization_id,
                            v_run.owner_id, v_run.next_event_sequence, 'run.interrupted',
                            jsonb_build_object('finish_reason', 'worker_lost', 'text',
                                v_run.partial_text, 'prompt_tokens', NULL, 'completion_tokens',
                                NULL, 'eval_duration_ns', NULL),
                            now());
                    UPDATE run_resource_leases SET
                        owner_run_id = NULL, owner_worker_id = NULL, lease_until = NULL,
                        updated_at = now()
                        WHERE owner_run_id = v_run.id;
                    RETURN NEXT v_run;
                END IF;
            END LOOP;
        END;
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION newray_reclaim_stale_runs(p_grace_seconds INTEGER)
        RETURNS SETOF runs
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            v_run runs;
        BEGIN
            FOR v_run IN
                SELECT r.* FROM runs r
                    WHERE r.state = 'running' AND r.lease_until < now()
                    AND NOT EXISTS (
                        SELECT 1 FROM run_workers w
                            WHERE w.worker_id = r.lease_owner
                            AND w.last_heartbeat_at > now() - make_interval(secs => p_grace_seconds)
                    )
                    FOR UPDATE SKIP LOCKED
            LOOP
                UPDATE runs SET
                    state = 'interrupted',
                    finish_reason = 'worker_lost',
                    updated_at = now()
                    WHERE id = v_run.id AND lease_owner = v_run.lease_owner
                        AND fence = v_run.fence
                    RETURNING * INTO v_run;
                IF v_run.id IS NOT NULL THEN
                    UPDATE run_resource_leases SET
                        owner_run_id = NULL, owner_worker_id = NULL, lease_until = NULL,
                        updated_at = now()
                        WHERE owner_run_id = v_run.id;
                    RETURN NEXT v_run;
                END IF;
            END LOOP;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION newray_finalize_run(
            p_run_id UUID, p_worker UUID, p_fence BIGINT, p_state TEXT,
            p_finish_reason TEXT, p_prompt_tokens INTEGER, p_completion_tokens INTEGER,
            p_eval_duration_ns BIGINT, p_partial_text TEXT, p_resource TEXT
        ) RETURNS runs
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            v_run runs;
        BEGIN
            UPDATE runs SET
                state = p_state,
                finish_reason = p_finish_reason,
                prompt_tokens = p_prompt_tokens,
                completion_tokens = p_completion_tokens,
                eval_duration_ns = p_eval_duration_ns,
                partial_text = p_partial_text,
                updated_at = now()
                WHERE id = p_run_id AND lease_owner = p_worker AND fence = p_fence
                    AND state = 'running'
                RETURNING * INTO v_run;
            IF v_run.id IS NULL THEN
                RETURN NULL;
            END IF;

            UPDATE run_resource_leases SET
                owner_run_id = NULL, owner_worker_id = NULL, lease_until = NULL,
                updated_at = now()
                WHERE resource_id = p_resource AND owner_worker_id = p_worker
                    AND owner_run_id = p_run_id;

            RETURN v_run;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION newray_heartbeat_run(
            p_run_id UUID, p_worker UUID, p_fence BIGINT, p_lease_seconds INTEGER,
            p_partial_text TEXT, p_resource TEXT
        ) RETURNS runs
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            v_run runs;
        BEGIN
            UPDATE runs SET
                partial_text = p_partial_text,
                lease_until = now() + make_interval(secs => p_lease_seconds),
                updated_at = now()
                WHERE id = p_run_id AND lease_owner = p_worker AND fence = p_fence
                    AND state = 'running'
                RETURNING * INTO v_run;
            IF v_run.id IS NULL THEN
                RETURN NULL;
            END IF;

            UPDATE run_resource_leases SET
                lease_until = now() + make_interval(secs => p_lease_seconds),
                updated_at = now()
                WHERE resource_id = p_resource AND owner_worker_id = p_worker
                    AND owner_run_id = p_run_id;

            RETURN v_run;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION newray_claim_run(
            p_worker UUID, p_lease_seconds INTEGER, p_resource TEXT
        ) RETURNS runs
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            v_run runs;
            v_lease run_resource_leases;
        BEGIN
            SELECT * INTO v_lease FROM run_resource_leases
                WHERE resource_id = p_resource FOR UPDATE;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            IF v_lease.lease_until IS NOT NULL AND v_lease.lease_until > now()
                AND v_lease.owner_worker_id IS DISTINCT FROM p_worker THEN
                RETURN NULL;
            END IF;

            SELECT * INTO v_run FROM runs WHERE state = 'queued'
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1;
            IF v_run.id IS NULL THEN
                RETURN NULL;
            END IF;

            UPDATE run_resource_leases SET
                owner_worker_id = p_worker,
                owner_run_id = v_run.id,
                lease_until = now() + make_interval(secs => p_lease_seconds),
                fence = fence + 1,
                updated_at = now()
                WHERE resource_id = p_resource;

            UPDATE runs SET
                state = 'running',
                lease_owner = p_worker,
                lease_until = now() + make_interval(secs => p_lease_seconds),
                fence = fence + 1,
                updated_at = now()
                WHERE id = v_run.id
                RETURNING * INTO v_run;

            RETURN v_run;
        END;
        $$
        """
    )
    op.execute("DROP TABLE run_events")
    op.execute("ALTER TABLE runs DROP CONSTRAINT runs_id_owner")
    op.execute("ALTER TABLE runs DROP CONSTRAINT runs_id_organization")
    op.execute("ALTER TABLE runs DROP COLUMN cancel_requested_at")
    op.execute("ALTER TABLE runs DROP COLUMN next_event_sequence")
