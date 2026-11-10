-- 002_inbox.sql (RQ-79, 81, 97, 38, 109): LLD-saathi-data section 2, verbatim. Runs as saathi_migrator.
CREATE SCHEMA IF NOT EXISTS control;
CREATE FUNCTION control.apply_tenant_rls(t regclass, append_only boolean DEFAULT false) RETURNS void
LANGUAGE plpgsql AS $f$
DECLARE col text := CASE WHEN t = 'households'::regclass THEN 'id' ELSE 'household_id' END;
        cond text := format('%I = current_setting(''app.household_id'', true)', col);
BEGIN
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
  EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t);
  EXECUTE format('CREATE POLICY tenant_sel ON %s FOR SELECT USING (%s)', t, cond);
  EXECUTE format('CREATE POLICY tenant_ins ON %s FOR INSERT WITH CHECK (%s)', t, cond);
  IF NOT append_only THEN
    EXECUTE format('CREATE POLICY tenant_upd ON %s FOR UPDATE USING (%s) WITH CHECK (%s)', t, cond, cond);
    EXECUTE format('CREATE POLICY tenant_del ON %s FOR DELETE USING (%s)', t, cond);
  END IF;
END $f$;

SELECT control.apply_tenant_rls(t) FROM unnest(ARRAY['households','elders','approvers','addresses',
  'profile_facts','requests','quotes','commits','runtime_flags']::regclass[]) AS t;

CREATE TABLE elder_identities (          -- created here, not in 012 (C-5)
  id text PRIMARY KEY, household_id text NOT NULL REFERENCES households(id),
  elder_id text NOT NULL REFERENCES elders(id),
  channel text NOT NULL CHECK (channel IN ('whatsapp','telegram_elder','caregiver_forward')),
  sender_hmac text NOT NULL UNIQUE,      -- under SENDER_HMAC_KEY (C-4)
  person_ref text NOT NULL UNIQUE,       -- opaque PersonRef the core holds
  person_ref_enc bytea NOT NULL,         -- sealed platform address
  key_id text NOT NULL, registered_at timestamptz NOT NULL DEFAULT now(), active boolean NOT NULL DEFAULT true);
ALTER TABLE elders DROP COLUMN wa_phone_e164;   -- E: plaintext phone, 001:15 (C-5)

CREATE TABLE inbox (
  id text PRIMARY KEY, household_id text NOT NULL REFERENCES households(id),
  provider_message_id text NOT NULL,     -- 'wa:<id>', 'te:<chat>:<msg>', 'cf:<chat>:<msg>'
  source text NOT NULL CHECK (source IN ('whatsapp','telegram_elder','caregiver_forward')),
  sender_hmac text NOT NULL, elder_id text NOT NULL REFERENCES elders(id),
  kind text NOT NULL CHECK (kind IN ('text','audio','image','video','document','unsupported')),
  payload_sha256 text NOT NULL, media_ref text, media_mime text,
  state text NOT NULL DEFAULT 'received'
    CHECK (state IN ('received','leased','processed','unsupported','media_lost','dead')),
  attempts smallint NOT NULL DEFAULT 0, lease_until timestamptz, lease_token uuid,
  not_before timestamptz, acked_at timestamptz, radar_at timestamptz,
  received_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (household_id, provider_message_id));   -- the only dedupe
CREATE INDEX inbox_queue ON inbox (elder_id, received_at, id) WHERE state IN ('received','leased');
CREATE INDEX inbox_unacked ON inbox (received_at) WHERE acked_at IS NULL AND state = 'received';

CREATE TABLE raw_text (
  inbox_id text NOT NULL REFERENCES inbox(id), household_id text NOT NULL REFERENCES households(id),
  part text NOT NULL CHECK (part IN ('text','caption','transcript')),
  body_sealed bytea NOT NULL, key_id text NOT NULL,   -- sealed box, AAD household|inbox|part (section 4)
  expires_at timestamptz NOT NULL, PRIMARY KEY (inbox_id, part));

SELECT control.apply_tenant_rls(t) FROM unnest(ARRAY['elder_identities','inbox','raw_text']::regclass[]) AS t;

CREATE TABLE rejected_inbound (          -- tenantless: the sender is unknown (C-6)
  id bigserial PRIMARY KEY, sender_hmac text NOT NULL, reason text NOT NULL, at timestamptz NOT NULL DEFAULT now());
ALTER TABLE rejected_inbound ENABLE ROW LEVEL SECURITY; ALTER TABLE rejected_inbound FORCE ROW LEVEL SECURITY;
CREATE POLICY rej_ins ON rejected_inbound FOR INSERT WITH CHECK (true);
CREATE POLICY rej_sel ON rejected_inbound FOR SELECT USING (false);   -- read only through control.rejected_digest()

ALTER TABLE requests DROP COLUMN provider_message_id, DROP COLUMN transcript_redacted, DROP COLUMN outcome,
  ADD COLUMN inbox_id text REFERENCES inbox(id),
  ADD COLUMN state text NOT NULL DEFAULT 'new' CHECK (state IN ('new','received','clarifying','quoting',
    'awaiting_approval','approved','committing','done','blocked','failed','handoff','expired','cancelled',
    'superseded','rejected','timeout','commit_unknown')),
  ADD COLUMN state_reason text, ADD COLUMN questions_asked smallint NOT NULL DEFAULT 0,
  ADD COLUMN urgent boolean NOT NULL DEFAULT false, ADD COLUMN expires_at timestamptz,
  ADD COLUMN superseded_by text REFERENCES requests(id),
  ADD COLUMN slots jsonb NOT NULL DEFAULT '{}', ADD COLUMN quote_id text, ADD COLUMN plan_step text;
