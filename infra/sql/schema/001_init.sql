-- Saathi core schema. Multi-household from day 1.
-- Personal values (names, numbers, addresses) live only in this database,
-- never in the repo.

CREATE TABLE households (
  id            text PRIMARY KEY,
  name          text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE elders (
  id            text PRIMARY KEY,
  household_id  text NOT NULL REFERENCES households(id),
  display_name  text NOT NULL,
  wa_phone_e164 text NOT NULL UNIQUE,
  languages     text[] NOT NULL DEFAULT ARRAY['hi','en'],
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE approvers (
  id               text PRIMARY KEY,
  household_id     text NOT NULL REFERENCES households(id),
  name             text NOT NULL,
  telegram_user_id bigint NOT NULL,
  is_primary       boolean NOT NULL DEFAULT false,
  UNIQUE (household_id, telegram_user_id)
);

-- Structured profile: the source of truth for addresses, preferences and
-- policies. Changes proposed by the agent need an approver's OK (status).
CREATE TABLE addresses (
  id            text PRIMARY KEY,
  household_id  text NOT NULL REFERENCES households(id),
  label         text NOT NULL,              -- 'home', 'doctor', 'temple', ...
  formatted     text NOT NULL,
  lat           double precision,
  lng           double precision,
  commit_allowed boolean NOT NULL DEFAULT false  -- the policy engine's address allowlist
);

CREATE TABLE profile_facts (
  id            bigserial PRIMARY KEY,
  household_id  text NOT NULL REFERENCES households(id),
  elder_id      text REFERENCES elders(id),
  key           text NOT NULL,              -- 'ride_policy', 'usual_atta', 'diet', ...
  value         jsonb NOT NULL,
  source        text NOT NULL,              -- 'caregiver' | 'agent:<trace_id>'
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('proposed','active','rejected')),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON profile_facts (household_id, key) WHERE status = 'active';

-- Every inbound message plus what we understood (the issue taxonomy feeds the
-- v2 dataset).
CREATE TABLE requests (
  id                  text PRIMARY KEY,
  household_id        text NOT NULL REFERENCES households(id),
  elder_id            text REFERENCES elders(id),
  channel             text NOT NULL,
  provider_message_id text NOT NULL UNIQUE,
  kind                text NOT NULL,
  transcript_redacted text,
  intent              text,
  confidence          real,
  tier_used           text,
  issue_category      text,
  radar               jsonb,
  outcome             text,                 -- 'summarised','committed','rejected','expired','alert'
  trace_id            text,
  received_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE quotes (
  id            text PRIMARY KEY,
  request_id    text NOT NULL REFERENCES requests(id),
  household_id  text NOT NULL REFERENCES households(id),
  quote_hash    text NOT NULL,
  body          jsonb NOT NULL,
  total_inr     numeric(10,2) NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE approvals (
  id                  bigserial PRIMARY KEY,
  quote_id            text NOT NULL REFERENCES quotes(id),
  approver_id         text NOT NULL REFERENCES approvers(id),
  decision            text NOT NULL CHECK (decision IN ('approve','edit','reject')),
  second_confirmation boolean NOT NULL DEFAULT false,
  reason              text,
  token               jsonb,                 -- signed ApprovalToken (approve only)
  decided_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE commits (
  id             bigserial PRIMARY KEY,
  quote_id       text NOT NULL REFERENCES quotes(id),
  household_id   text NOT NULL REFERENCES households(id),
  service        text NOT NULL,
  skus           text[] NOT NULL DEFAULT '{}',
  amount_inr     numeric(10,2) NOT NULL,
  provider_ref   text,
  committed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON commits (household_id, committed_at);

-- Paid model spend: the gateway's hard monthly cap.
CREATE TABLE llm_spend (
  id          bigserial PRIMARY KEY,
  tier        text NOT NULL,
  model       text NOT NULL,
  cost_inr    numeric(10,4) NOT NULL,
  trace_id    text,
  spent_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE runtime_flags (
  household_id text PRIMARY KEY REFERENCES households(id),
  paused       boolean NOT NULL DEFAULT false,
  updated_at   timestamptz NOT NULL DEFAULT now()
);
