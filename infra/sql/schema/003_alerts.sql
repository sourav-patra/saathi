-- 003_alerts.sql (RQ-18, 23): LLD-saathi-data section 2, verbatim. Runs as saathi_migrator.
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
