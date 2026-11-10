---
id: LLD-saathi-data
kind: contract
governs: Phase 0 migrations 002 to 005 (tables), row-level security and household keys; part of LLD-saathi-2026-11-10.md; roles, functions and grants are in LLD-saathi-grants-2026-11-10.md
status: draft, pending review
upstream: TRD-saathi-ports-2026-11-09.md sha256 993e28969a4c98164e04767cd324f88930b1e73dbc2a854f74a725be1163ec3c; SPEC-saathi-platform-2026-11-10.md sha256 2ccd496e0116592e2461f70d0fdbe2190dd8373e11afda13b53206be1c303a99; POLICY-saathi-data-2026-11-09.md sha256 907e2bd010ffdebe8e22a43fd693258be096922972c1846bf73f64593bf3f6c4; LLD-saathi-2026-11-10.md sha256 bda71cce7ddb7c9fe5b18223d08d360f881cfc660c1d3befaf70294488692309
sources: infra/sql/schema/001_init.sql and 00-create-databases.sh at 3d46456; the TRD ports part sections 4 and 5
checked-at: 3d46456, 2026-11-10. 001 read with `git show`; the SQL of sections 2 ran on PostgreSQL 16.15 in a scratch instance on 2026-11-10 (hub section 6).
---

Read section 1 for the table rules, 2 for the four migrations, 3 for where roles and grants moved, 4 for keys.

# Saathi LLD data: Phase 0 schema, row-level security and keys

## 1. Rules for every table

All SQL is P; E facts cite 001 at 3d46456. Files live in `infra/sql/schema/` and run in order by `saathi.store.migrate` as `saathi_migrator`, forward only, one transaction each.

1. A household table has `household_id text NOT NULL REFERENCES households(id)` and, in its migration, a call `control.apply_tenant_rls('<table>')`, which enables and forces row-level security and adds one policy per command testing `household_id = current_setting('app.household_id', true)`. An unset setting is NULL, so no row passes. `households` tests `id`.
2. Append-only tables (`audit_log`, `consent_events`) get select and insert policies only, so update and delete fail even with a grant.
3. The edge never reads or uses RETURNING: ids come from `new_id()`, a conflict shows as the tag `INSERT 0 0`.
4. Every function that scans households is SECURITY DEFINER, owned by `saathi_scan`, pinned to `search_path = pg_catalog, public`, takes `p_now` (never `now()`, so a fake clock works) and returns ids only (RQ-109).
5. Encrypted values carry `key_id` beside them; keyed hashes carry `key_id` too (RQ-111a).
6. `tests/test_schema.py` reads `pg_class` and fails on a table with a `household_id` column and no forced row-level security, and on a table with no such column that is not in `TENANTLESS = {rejected_inbound, voice_clips, heartbeats, schema_migrations}` (C-6, C-8).

## 2. Migrations

### 002_inbox.sql (RQ-79, 81, 97, 38, 109)

```sql
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
```

E: `requests.intent` exists (001:63) and stays. A request's first id is `'r_' || inbox_id`, so a retried step inserts it with `ON CONFLICT (id) DO NOTHING`.

### 003_alerts.sql (RQ-18, 23)

```sql
CREATE TABLE alert_events (
  id text PRIMARY KEY, household_id text NOT NULL REFERENCES households(id),
  elder_id text NOT NULL REFERENCES elders(id), inbox_id text NOT NULL REFERENCES inbox(id),
  kind text NOT NULL CHECK (kind IN ('emergency','scam','unscanned')),
  origin text NOT NULL CHECK (origin IN ('radar_edge','radar_worker','classifier','unscanned_edge','unscanned_overdue')),
  state text NOT NULL DEFAULT 'open' CHECK (state IN ('open','acknowledged','blocked')),
  stage text NOT NULL DEFAULT 'a' CHECK (stage IN ('a','b','c')),
  rule_ids text[] NOT NULL DEFAULT '{}', t0 timestamptz NOT NULL, next_due_at timestamptz,
  acked_by text, acked_at timestamptz, false_alarm boolean NOT NULL DEFAULT false,
  audio_ref text, blocked_reason text, followup_due_at timestamptz, last_elder_clip_at timestamptz,
  UNIQUE (household_id, inbox_id, kind));       -- edge and worker cannot both open one
CREATE INDEX alert_due ON alert_events (next_due_at) WHERE state = 'open';
CREATE TABLE alert_attempts (
  id bigserial PRIMARY KEY, household_id text NOT NULL REFERENCES households(id),
  event_id text NOT NULL REFERENCES alert_events(id), stage text NOT NULL CHECK (stage IN ('a','b','c')),
  approver_id text NOT NULL REFERENCES approvers(id), channel text NOT NULL,
  idem_key text NOT NULL, due_at timestamptz NOT NULL, sent_at timestamptz,
  status text NOT NULL DEFAULT 'due' CHECK (status IN ('due','sent','delivered','failed','cancelled',
    'answered_no_ack','voicemail','acked_voice')),
  provider_ref_hmac text, UNIQUE (household_id, idem_key));  -- 'alert:<event>:<stage>:<approver>:<n>'
CREATE TABLE tg_updates (
  bot text NOT NULL CHECK (bot IN ('caregiver','elder')), update_id bigint NOT NULL,
  household_id text NOT NULL REFERENCES households(id), kind text NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (bot, update_id));  -- ids repeat across bots
SELECT control.apply_tenant_rls(t) FROM unnest(ARRAY['alert_events','alert_attempts','tg_updates']::regclass[]) AS t;
```

### 004_audit_control.sql (RQ-56a, 95a, 110, 111a; PL-05, 33, 34)

```sql
CREATE TABLE audit_log (
  seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  household_id text NOT NULL REFERENCES households(id), hseq bigint,      -- chain columns: 013
  ts timestamptz NOT NULL DEFAULT now(), run_id text NOT NULL,
  actor text NOT NULL CHECK (actor ~ '^(elder|approver:.+|system:.+|agent:.+)$'),
  event text NOT NULL, subject text, payload_hmac text, key_id text NOT NULL,
  detail jsonb NOT NULL DEFAULT '{}', prev_hash text, row_hash text);      -- detail: codes only
CREATE INDEX audit_household ON audit_log (household_id, seq);

CREATE TABLE control.signoffs (
  id bigserial PRIMARY KEY, household_id text NOT NULL REFERENCES households(id),
  scope text NOT NULL CHECK (scope = 'SO-1'),         -- others: 011
  granter text NOT NULL, granted_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL, revoked_at timestamptz, CHECK (expires_at > granted_at));
CREATE TABLE control.consent_events (
  id bigserial PRIMARY KEY, household_id text NOT NULL REFERENCES households(id),
  subject text NOT NULL CHECK (subject IN ('elder','caregiver')), use text NOT NULL, recipient text NOT NULL,
  event text NOT NULL CHECK (event IN ('grant','withdraw')),
  capacity text NOT NULL CHECK (capacity IN ('self','guardian','unknown')), guardian_ref text,
  language text NOT NULL, how text NOT NULL CHECK (how IN ('spoken','written','guardian')),
  notice_text_sha256 text NOT NULL, recorded_by text NOT NULL, recorded_at timestamptz NOT NULL DEFAULT now(),
  CHECK (capacity <> 'guardian' OR guardian_ref IS NOT NULL));
CREATE TABLE control.household_config (
  household_id text NOT NULL REFERENCES households(id), version int NOT NULL CHECK (version > 0),
  values jsonb NOT NULL, created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, version));
CREATE TABLE control.household_keys (
  household_id text NOT NULL REFERENCES households(id),
  purpose text NOT NULL CHECK (purpose IN ('content','totp','audit')), key_id text NOT NULL,
  public_key bytea, wrapped_key bytea NOT NULL,           -- public_key only for 'content'
  created_at timestamptz NOT NULL DEFAULT now(), retired_at timestamptz,
  PRIMARY KEY (household_id, purpose, key_id),
  CHECK ((purpose = 'content') = (public_key IS NOT NULL)));
CREATE UNIQUE INDEX one_active_key ON control.household_keys (household_id, purpose) WHERE retired_at IS NULL;
SELECT control.apply_tenant_rls('audit_log', true);
SELECT control.apply_tenant_rls('control.consent_events', true);
SELECT control.apply_tenant_rls(t) FROM unnest(ARRAY['control.signoffs','control.household_config',
  'control.household_keys']::regclass[]) AS t;
```

`household_config.values` is validated by `HouseholdConfig` (M06). Migration 004 also appends the policies, functions and grants of LLD-saathi-grants-2026-11-10.md section 2; no migration creates a role.

### 005_ledger_voice_flags.sql (RQ-48, 32, 38; D-69, D-71)

```sql
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
```

`voice_clips` holds no household data (a clip holds no name or number, D-69), so it has no policy and joins `TENANTLESS` in the schema test. The loader refuses a row with `reviewed_by` NULL (`ClipUnreviewed`); only `saathi_owner`, after a receipt exists, sets the review columns. Migration 005 also appends `control.beat` and the grants of its tables (grants part, section 3).

## 3. Roles, functions and grants

They moved to LLD-saathi-grants-2026-11-10.md so that each file stays under its size limit. Role creation is in the superuser init script, never in a migration. Migrations 004 and 005 append the functions, policies, table grants and sequence grants of that file, each in the same transaction as the tables they cover. No role has `BYPASSRLS` except `saathi_readonly`. The cross-tenant suite (TQ-71) covers every table and role.

Sealed personal columns (D-91). Phase 0 holds synthetic data only. Before any real row, the first Phase 1 migration seals `addresses` (label, formatted, lat, lng), `profile_facts.value` and the display names of `elders`, `approvers` and `households` with the household `content` key, as `elder_identities.person_ref_enc` already is. Until then these columns stay readable by their roles under row-level security, and the consent gate blocks real data (C-5).

## 4. Household keys (RQ-111a)

One active key per household and purpose (index `one_active_key`).

| Purpose | Material | Protects | Held by |
|---|---|---|---|
| `content` | X25519 pair; `public_key` plain, private half in `wrapped_key` | `raw_text.body_sealed`, `elder_identities.person_ref_enc` | public half: edge and core; private half: core, after unwrap with the KEK |
| `audit` | 32 random bytes in `wrapped_key` | the HMAC of `audit_log.payload_hmac` and `subject` | core (unwrapped in memory) |
| `totp` | 32 random bytes in `wrapped_key` | Phase 1 TOTP secrets | created now, used from 014 |

Formats, all in `saathi/store/keys.py`:
1. Wrap: AES-256-GCM under the key-encryption key (KEK), random 12 byte nonce, AAD `household_id|purpose|key_id`; stored as nonce plus ciphertext.
2. Sealed box: byte `0x01`, ephemeral X25519 public key (32), nonce (12), ciphertext and tag; the AES-256-GCM key is HKDF-SHA256 of the shared secret, info `saathi.seal.v1`; AAD `household_id|inbox_id|part` (identities: `household_id|identity_id|ref`), so a blob copied to another row fails.
3. Keyed hash: HMAC-SHA256 over `domain|value`, hex, under the `audit` key; `key_id` beside it.
4. Sender hash: HMAC-SHA256 over `channel:sender id` under the deployment key `SENDER_HMAC_KEY` (the household is unknown at the edge; C-4).
5. The KEK is the file `KEK_FILE`, mounted into the worker only; the database, the edge and `saathi_model` never see it (PL-14).

Procedures, owner commands of `saathi-admin` on the host shell where `KEK_FILE` is readable (C-7):
1. `keys init <household>` (after `household create`) generates the three keys, wraps them, inserts them and prints the `key_id`s.
2. `keys rotate <household> <purpose>` for `content` or `audit`: in one transaction (owner holds `UPDATE (retired_at)` and `INSERT`) set `retired_at` on the active row, then insert the new row (the unique index allows one active key). New rows use the new key; old rows stay readable by their `key_id`. The 30 day expiry retires old `raw_text`; the Phase 1 job (RQ-111b) re-encrypts media and identities.
3. Rotate the KEK: `keys rewrap --old-file F1 --new-file F2` re-wraps every `wrapped_key` (owner holds `SELECT` and `UPDATE (wrapped_key)`, and reads all households through policy `owner_hh`); then `KEK_FILE` points at F2. A read before and after gives the same plaintext.
4. Erase a household (Phase 1, RQ-111b): destroy its three key rows after the erasure record exists.

Limits: the backup role and a holder of the KEK see every household (L-11); `SENDER_HMAC_KEY` is shared by households; `raw_text` expiry waits for the Phase 1 retention job (RQ-42).

Skipped or not checked: the key procedures and the sealed formats of section 4 are not code and did not run; the Phase 1 sealing migration is not written; the Python migrator was not run, the SQL files were applied with `psql`.
