-- 005_ledger_voice_flags.sql (RQ-48, 32, 38; D-69, D-71): LLD-saathi-data section 2 verbatim, then LLD-saathi-grants
-- section 3 (control.beat and the grants of the 005 tables), verbatim. Runs as saathi_migrator.
ALTER TABLE approvals ADD COLUMN household_id text REFERENCES households(id);   -- E: absent, 001:83-92
ALTER TABLE llm_spend ADD COLUMN household_id text REFERENCES households(id),
  ADD COLUMN reservation_id bigint;                                              -- E: absent, 001:107-114
ALTER TABLE runtime_flags ADD COLUMN paused_by text, ADD COLUMN reason text;
CREATE TABLE control.heartbeats (               -- tenantless (C-8); via control.beat()
  component text PRIMARY KEY, beat_at timestamptz NOT NULL, status text NOT NULL DEFAULT 'ok');
CREATE TABLE llm_reservations (
  id bigserial PRIMARY KEY, household_id text NOT NULL REFERENCES households(id), run_id text NOT NULL,
  tier text NOT NULL, reserved_inr numeric(10,4) NOT NULL CHECK (reserved_inr >= 0),
  state text NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved','settled','released')),
  settled_inr numeric(10,4), month text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE voice_clips (                           -- shared fixed clips: no household column (D-69)
  reply_key text NOT NULL, version int NOT NULL, language text NOT NULL, text text NOT NULL,
  sha256 text NOT NULL, path text NOT NULL, reviewed_by text, reviewed_at timestamptz, receipt_ref text,
  PRIMARY KEY (reply_key, version, language),
  CHECK ((reviewed_by IS NULL) = (reviewed_at IS NULL)));
ALTER TABLE approvals ALTER COLUMN household_id SET NOT NULL;   -- both tables are empty in Phase 0
ALTER TABLE llm_spend ALTER COLUMN household_id SET NOT NULL;
SELECT control.apply_tenant_rls(t) FROM unnest(ARRAY['approvals','llm_spend','llm_reservations']::regclass[]) AS t;

-- appended to 005 after its tables; runs as saathi_migrator
GRANT CREATE ON SCHEMA control TO saathi_scan;      -- only so ALTER FUNCTION ... OWNER TO works; revoked below
CREATE FUNCTION control.beat(p_component text, p_now timestamptz, p_status text DEFAULT 'ok')
RETURNS TABLE (core_at timestamptz, core_status text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $f$
BEGIN
  IF p_component <> replace(session_user, 'saathi_', '') OR p_component NOT IN ('edge', 'core') THEN
    RAISE EXCEPTION 'beat: component % does not match the calling role', p_component;
  END IF;
  INSERT INTO control.heartbeats (component, beat_at, status) VALUES (p_component, p_now, p_status)
    ON CONFLICT (component) DO UPDATE SET beat_at = EXCLUDED.beat_at, status = EXCLUDED.status;
  RETURN QUERY SELECT h.beat_at, h.status FROM control.heartbeats h WHERE h.component = 'core';
END $f$;
REVOKE EXECUTE ON FUNCTION control.beat(text, timestamptz, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.beat(text, timestamptz, text) TO saathi_edge, saathi_core;
ALTER FUNCTION control.beat(text, timestamptz, text) OWNER TO saathi_scan;
REVOKE CREATE ON SCHEMA control FROM saathi_scan;
GRANT SELECT, INSERT, UPDATE ON control.heartbeats TO saathi_scan;
GRANT SELECT, INSERT ON llm_spend TO saathi_model;
GRANT SELECT, INSERT ON llm_reservations TO saathi_model;
GRANT UPDATE (state, settled_inr) ON llm_reservations TO saathi_model;
GRANT USAGE ON SEQUENCE llm_spend_id_seq, llm_reservations_id_seq TO saathi_model;
GRANT SELECT ON voice_clips TO saathi_core;
GRANT SELECT, INSERT ON voice_clips TO saathi_owner;
GRANT UPDATE (reviewed_by, reviewed_at, receipt_ref) ON voice_clips TO saathi_owner;
GRANT SELECT ON llm_spend, llm_reservations, voice_clips, control.heartbeats TO saathi_readonly;
GRANT UPDATE (paused, paused_by, reason, updated_at) ON runtime_flags TO saathi_core;   -- the two new columns exist from this file
