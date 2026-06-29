-- Migration 184: agent_ro — read-only DB role for the in-cluster dev/coding agent (#302).
--
-- Mirrors twin_ro (155) but grants read-ALL via the PG16 built-in pg_read_all_data
-- (the agent inspects any table to do its work; it never writes prod). This is the
-- genuinely least-privilege answer to #302/#305: an agent (or Grafana) reads prod
-- through agent_ro instead of the `verdify` SUPERUSER exec path.
--
-- NOLOGIN group role only. The per-agent LOGIN user (`agent`) is created out-of-band
-- (RUNBOOK / kept out of the migration so no password lands in git) and GRANTed this
-- role; its DSN is sealed in deploy/k8s/overlays/prod/agent-ro-secret.sops.yaml.
--
-- Non-self-transactional (no top-level COMMIT; only the DO-block BEGIN) — safe to
-- rollback-validate by wrapping in an outer BEGIN; … ROLLBACK; per migration-safety.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_ro') THEN
        CREATE ROLE agent_ro NOLOGIN;
    END IF;
END
$$;

-- READ-ALL, NO WRITE: pg_read_all_data confers SELECT on every current and future
-- table plus USAGE on schemas, with no INSERT/UPDATE/DELETE/TRUNCATE. Idempotent
-- (GRANT of an already-held membership is a no-op).
GRANT pg_read_all_data TO agent_ro;

COMMENT ON ROLE agent_ro IS
    'Read-only DB role for the in-cluster dev/coding agent (#302). Member of pg_read_all_data (SELECT on all current+future tables; NO write of any kind). NOLOGIN group role; the per-agent LOGIN user (agent) is created out-of-band and GRANTed this role, DSN sealed in verdify-agent-secrets. Least-privilege: agents read prod without the verdify superuser exec path. Also the target role for down-scoping Grafana off superuser (#305).';
