"""P-05: worker durevole, lease/fencing e coda a risorsa limitata.

Il worker deve reclamare run di qualunque organizzazione (coda equa di
sistema), ma ``runs`` ha RLS FORCE scoped alla sessione: nessun ruolo
applicativo può attraversarla restando senza BYPASSRLS (ADR 0002). La
soluzione (ADR 0007) sono tre funzioni SQL ``SECURITY DEFINER`` possedute
dal ruolo interno ``newray_scheduler`` (NOLOGIN, creato fuori da questa
migrazione in ``backend/scripts/db/bootstrap.sql`` / ``ensure_database`` del
launcher, mai usato per connettersi). Le funzioni toccano solo le colonne
già concesse a ``newray_app`` in 0009; nessuna nuova superficie di
scrittura, solo attraversamento cross-org per operazioni auditabili.

``run_resource_leases`` e ``run_workers`` non contengono dati di tenant
(solo ID opachi di risorsa/worker): nessuna RLS, grant minimi.
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL richiede che il NUOVO proprietario di un oggetto abbia
    # CREATE sullo schema che lo contiene (non solo chi esegue l'ALTER
    # OWNER più sotto): newray_scheduler non lo eredita da nessun ruolo
    # (non possiede il database), quindi va concesso qui, per database,
    # come già fa 0001 con USAGE per newray_app.
    op.execute("GRANT CREATE, USAGE ON SCHEMA public TO newray_scheduler")

    op.execute(
        """
        CREATE TABLE run_resource_leases (
            resource_id TEXT PRIMARY KEY,
            owner_run_id UUID,
            owner_worker_id UUID,
            fence BIGINT NOT NULL DEFAULT 0 CHECK (fence >= 0),
            lease_until TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        "INSERT INTO run_resource_leases (resource_id, fence, updated_at) "
        "VALUES ('gpu:0', 0, now())"
    )

    op.execute(
        """
        CREATE TABLE run_workers (
            worker_id UUID PRIMARY KEY,
            pid INTEGER NOT NULL,
            hostname TEXT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL,
            last_heartbeat_at TIMESTAMPTZ NOT NULL
        )
        """
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON run_resource_leases TO newray_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON run_workers TO newray_app")

    # Le funzioni SECURITY DEFINER girano con i privilegi del PROPRIETARIO
    # (newray_scheduler): BYPASSRLS aggira la row-level security, non i
    # GRANT di tabella, che restano necessari qui, indipendenti da quelli
    # già concessi a newray_app. SELECT su ``runs`` è senza restrizione di
    # colonna (il worker deve leggere lo snapshot per eseguire il run);
    # l'UPDATE resta sulle sole colonne di gestione coda, identiche a 0009.
    op.execute("GRANT SELECT ON runs TO newray_scheduler")
    op.execute(
        "GRANT UPDATE (state, partial_text, finish_reason, prompt_tokens, "
        "completion_tokens, eval_duration_ns, lease_owner, lease_until, fence, updated_at) "
        "ON runs TO newray_scheduler"
    )
    op.execute("GRANT SELECT, UPDATE ON run_resource_leases TO newray_scheduler")
    op.execute("GRANT SELECT ON run_workers TO newray_scheduler")

    # -- claim: transazione breve, SKIP LOCKED, claim del resource lease. --
    op.execute(
        """
        CREATE FUNCTION newray_claim_run(
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

    # -- heartbeat: rinnova lease + checkpoint, fencing-checked. --
    op.execute(
        """
        CREATE FUNCTION newray_heartbeat_run(
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

    # -- finalize: transizione terminale fencing-checked, rilascia risorsa. --
    op.execute(
        """
        CREATE FUNCTION newray_finalize_run(
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

    # -- reclaim: lease scaduto E worker verificato morto (heartbeat stale
    # o riga assente). Transizione a interrupted, parziale preservato,
    # nessuna riesecuzione automatica (niente retry ciechi). --
    op.execute(
        """
        CREATE FUNCTION newray_reclaim_stale_runs(p_grace_seconds INTEGER)
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

    for function_signature in (
        "newray_claim_run(UUID, INTEGER, TEXT)",
        "newray_heartbeat_run(UUID, UUID, BIGINT, INTEGER, TEXT, TEXT)",
        "newray_finalize_run(UUID, UUID, BIGINT, TEXT, TEXT, INTEGER, INTEGER, BIGINT, TEXT, TEXT)",
        "newray_reclaim_stale_runs(INTEGER)",
    ):
        op.execute(f"ALTER FUNCTION {function_signature} OWNER TO newray_scheduler")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function_signature} TO newray_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION newray_reclaim_stale_runs(INTEGER)")
    op.execute(
        "DROP FUNCTION newray_finalize_run("
        "UUID, UUID, BIGINT, TEXT, TEXT, INTEGER, INTEGER, BIGINT, TEXT, TEXT)"
    )
    op.execute("DROP FUNCTION newray_heartbeat_run(UUID, UUID, BIGINT, INTEGER, TEXT, TEXT)")
    op.execute("DROP FUNCTION newray_claim_run(UUID, INTEGER, TEXT)")
    op.execute("DROP TABLE run_workers")
    op.execute("DROP TABLE run_resource_leases")
