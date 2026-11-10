---
id: LLD-saathi-grants
kind: contract
governs: Phase 0 database roles, the init script, security-definer functions, policies, grants and sequence grants; part of LLD-saathi-2026-11-10.md
status: draft, pending owner lock (see docs/LOCKS.md)
upstream: TRD-saathi-ports-2026-11-09.md sha256 993e28969a4c98164e04767cd324f88930b1e73dbc2a854f74a725be1163ec3c; POLICY-saathi-data-2026-11-09.md sha256 907e2bd010ffdebe8e22a43fd693258be096922972c1846bf73f64593bf3f6c4; LLD-saathi-2026-11-10.md sha256 bda71cce7ddb7c9fe5b18223d08d360f881cfc660c1d3befaf70294488692309; LLD-saathi-data-2026-11-10.md sha256 93e99348d47a446115cb78f894cd85d2e39cee43c884921e8e4ff48f40de7307
sources: infra/sql/00-create-databases.sh at 3d46456; the TRD ports part section 5; review I of the design set; the SQL below, run on PostgreSQL 16.15 in a scratch instance (section 6)
checked-at: 3d46456 for the code; the SQL ran on 2026-11-10 with the smoke test of section 6
---

Read section 1 for the init script, sections 2 and 3 for the SQL that migrations 004 and 005 append, section 4 to check a module call against a grant.

# Saathi LLD grants: roles, functions, policies and grants

## 1. Role creation: `infra/sql/00-create-databases.sh` (build change)

Only a superuser can create roles or set `BYPASSRLS`, and `saathi_migrator` is not one. The init script therefore creates every role and the database; no migration creates a role (review I). The script runs once at first start of Postgres; its role section is below, and its existing `CREATE DATABASE langfuse` and `CREATE EXTENSION vector` lines stay (E: `00-create-databases.sh:5-9`). It no longer runs `001_init.sql`: `saathi-migrate` applies 001 to 005 as `saathi_migrator`. A one-off `REASSIGN OWNED BY saathi TO saathi_migrator` serves an old database.

```sql
CREATE ROLE saathi_migrator LOGIN PASSWORD '${SAATHI_DB_PASSWORD_MIGRATOR}' NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE ROLE saathi_edge     LOGIN PASSWORD '${SAATHI_DB_PASSWORD_EDGE}'     NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE ROLE saathi_core     LOGIN PASSWORD '${SAATHI_DB_PASSWORD_CORE}'     NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE ROLE saathi_model    LOGIN PASSWORD '${SAATHI_DB_PASSWORD_MODEL}'    NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE ROLE saathi_owner    LOGIN PASSWORD '${SAATHI_DB_PASSWORD_OWNER}'    NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE ROLE saathi_readonly LOGIN PASSWORD '${SAATHI_DB_PASSWORD_READONLY}' NOSUPERUSER NOCREATEROLE BYPASSRLS;
CREATE ROLE saathi_scan  NOLOGIN NOSUPERUSER NOCREATEROLE NOBYPASSRLS;   -- owns the scan functions
CREATE ROLE saathi_audit NOLOGIN NOSUPERUSER NOCREATEROLE NOBYPASSRLS;   -- owns audit_append
GRANT saathi_scan, saathi_audit TO saathi_migrator WITH INHERIT FALSE, SET TRUE;   -- lets 004 hand over function ownership
CREATE DATABASE saathi OWNER saathi_migrator;
REVOKE ALL ON DATABASE saathi FROM PUBLIC;
GRANT CONNECT ON DATABASE saathi TO saathi_edge, saathi_core, saathi_model, saathi_owner, saathi_readonly;
```

`saathi_scan` and `saathi_audit` own functions and never log in. The `GRANT` of both to the migrator lets migration 004 hand function ownership over. Each `${...}` is a separate secret variable (PL-14); the test run used fixed words in their place.

## 2. Migration 004 appends: policies, functions, grants

The migrator owns every table. The functions are SECURITY DEFINER with a fixed `search_path` and take `p_now`, never `now()`. A function that loops households sets `app.household_id` for each one and restores the caller's value before it returns, so the setting does not leak to the caller's transaction.

Order matters, and the run found three traps. `ALTER DEFAULT PRIVILEGES ... IN SCHEMA` cannot remove the built-in `EXECUTE` of `PUBLIC`; only the form with no schema can. A role that is no longer the owner cannot grant, so every `EXECUTE` grant comes before `ALTER FUNCTION ... OWNER TO`. Ownership transfer needs `CREATE` on the schema for the new owner, which is granted for that step and revoked after.

```sql
-- appended to 004_audit_control.sql; runs as saathi_migrator
GRANT USAGE ON SCHEMA control TO saathi_edge, saathi_core, saathi_owner, saathi_readonly, saathi_scan, saathi_audit;
REVOKE ALL ON ALL TABLES IN SCHEMA public, control FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public, control FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE saathi_migrator REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;   -- no IN SCHEMA: that form cannot remove the built-in grant
GRANT CREATE ON SCHEMA control TO saathi_scan, saathi_audit;   -- only so ALTER FUNCTION ... OWNER TO works; revoked below

-- policies for the functions (tenant policies still apply wherever the household setting is set)
CREATE POLICY scan_hh    ON households       FOR SELECT TO saathi_scan USING (true);
CREATE POLICY owner_hh   ON households       FOR SELECT TO saathi_owner USING (true);
CREATE POLICY scan_ident ON elder_identities FOR SELECT TO saathi_scan USING (true);
CREATE POLICY scan_appr  ON approvers        FOR SELECT TO saathi_scan USING (true);
CREATE POLICY rej_dig    ON rejected_inbound FOR SELECT TO saathi_scan USING (true);

CREATE FUNCTION control.resolve_sender(p_hmac text) RETURNS TABLE (household_id text, elder_id text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $f$
  SELECT i.household_id, i.elder_id FROM public.elder_identities i WHERE i.sender_hmac = p_hmac AND i.active LIMIT 1 $f$;

CREATE FUNCTION control.resolve_approver(p_telegram_id bigint) RETURNS TABLE (household_id text, approver_id text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $f$
  SELECT a.household_id, a.id FROM public.approvers a WHERE a.telegram_user_id = p_telegram_id LIMIT 1 $f$;

CREATE FUNCTION control.rejected_digest(p_since timestamptz) RETURNS TABLE (distinct_senders int, total int)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $f$
  SELECT count(DISTINCT sender_hmac)::int, count(*)::int FROM public.rejected_inbound WHERE at >= p_since $f$;

CREATE FUNCTION control.rate_state(p_now timestamptz, p_elder text) RETURNS timestamptz
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $f$
DECLARE hh text := current_setting('app.household_id', true);
        lim int := 20; win int := 600; n int; oldest timestamptz; cfg jsonb;
BEGIN
  IF hh IS NULL OR hh = '' THEN RAISE EXCEPTION 'TenantNotSet'; END IF;
  SELECT c.values INTO cfg FROM control.household_config c WHERE c.household_id = hh ORDER BY c.version DESC LIMIT 1;
  lim := coalesce((cfg->>'rate_limit_msgs')::int, lim);
  win := coalesce((cfg->>'rate_limit_window_s')::int, win);
  SELECT count(*), min(i.received_at) INTO n, oldest FROM public.inbox i
   WHERE i.household_id = hh AND i.elder_id = p_elder AND i.received_at > p_now - make_interval(secs => win);
  IF n >= lim THEN RETURN oldest + make_interval(secs => win); END IF;
  RETURN NULL;
END $f$;

CREATE FUNCTION control.audit_append(p_run text, p_actor text, p_event text, p_subject text, p_payload_hmac text,
                                     p_key_id text, p_detail jsonb) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $f$
DECLARE hh text := current_setting('app.household_id', true); s bigint;
BEGIN
  IF hh IS NULL OR hh = '' THEN RAISE EXCEPTION 'TenantNotSet'; END IF;
  INSERT INTO public.audit_log (household_id, run_id, actor, event, subject, payload_hmac, key_id, detail)
  VALUES (hh, p_run, p_actor, p_event, p_subject, p_payload_hmac, p_key_id, coalesce(p_detail, '{}'::jsonb))
  RETURNING seq INTO s;
  RETURN s;
END $f$;

CREATE FUNCTION control.lease_next(p_now timestamptz, p_lease_s int)
RETURNS TABLE (household_id text, inbox_id text, elder_id text, lease_token uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $f$
DECLARE h record; r record; saved text := current_setting('app.household_id', true); tok uuid;
BEGIN
  FOR h IN SELECT x.id FROM public.households x ORDER BY x.id LOOP
    PERFORM set_config('app.household_id', h.id, true);
    FOR r IN
      SELECT DISTINCT ON (i.elder_id) i.id AS iid, i.elder_id AS eid FROM public.inbox i
       WHERE (i.state = 'received' OR (i.state = 'leased' AND i.lease_until < p_now))
         AND (i.not_before IS NULL OR i.not_before <= p_now) AND i.attempts < 5
         AND NOT EXISTS (SELECT 1 FROM public.inbox l WHERE l.elder_id = i.elder_id
                          AND l.state = 'leased' AND l.lease_until >= p_now)
       ORDER BY i.elder_id, i.received_at, i.id
    LOOP
      CONTINUE WHEN NOT pg_try_advisory_xact_lock(hashtextextended(r.eid, 0));
      tok := gen_random_uuid();
      UPDATE public.inbox SET state = 'leased', lease_until = p_now + make_interval(secs => p_lease_s),
             lease_token = tok, attempts = attempts + 1
       WHERE id = r.iid AND (state = 'received' OR lease_until < p_now);
      IF FOUND THEN
        household_id := h.id; inbox_id := r.iid; elder_id := r.eid; lease_token := tok;
        PERFORM set_config('app.household_id', coalesce(saved, ''), true);
        RETURN NEXT; RETURN;
      END IF;
    END LOOP;
  END LOOP;
  PERFORM set_config('app.household_id', coalesce(saved, ''), true);
END $f$;

CREATE FUNCTION control.reap_inbox(p_now timestamptz) RETURNS TABLE (household_id text, inbox_id text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $f$
DECLARE h record; saved text := current_setting('app.household_id', true);
BEGIN
  FOR h IN SELECT x.id FROM public.households x LOOP
    PERFORM set_config('app.household_id', h.id, true);
    RETURN QUERY UPDATE public.inbox i SET state = 'dead'
      WHERE i.state = 'leased' AND i.lease_until < p_now AND i.attempts >= 5 RETURNING i.household_id, i.id;
  END LOOP;
  PERFORM set_config('app.household_id', coalesce(saved, ''), true);
END $f$;

CREATE FUNCTION control.unacked_inbox(p_now timestamptz, p_limit int) RETURNS TABLE (household_id text, inbox_id text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $f$
DECLARE h record; saved text := current_setting('app.household_id', true);
BEGIN
  FOR h IN SELECT x.id FROM public.households x LOOP
    PERFORM set_config('app.household_id', h.id, true);
    RETURN QUERY SELECT i.household_id, i.id FROM public.inbox i
      WHERE i.acked_at IS NULL AND i.state IN ('received', 'leased') AND i.received_at <= p_now LIMIT p_limit;
  END LOOP;
  PERFORM set_config('app.household_id', coalesce(saved, ''), true);
END $f$;

CREATE FUNCTION control.due_alerts(p_now timestamptz, p_limit int) RETURNS TABLE (household_id text, event_id text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $f$
DECLARE h record; saved text := current_setting('app.household_id', true);
BEGIN
  FOR h IN SELECT x.id FROM public.households x LOOP
    PERFORM set_config('app.household_id', h.id, true);
    RETURN QUERY SELECT a.household_id, a.id FROM public.alert_events a
      WHERE a.state = 'open' AND a.next_due_at <= p_now LIMIT p_limit;
  END LOOP;
  PERFORM set_config('app.household_id', coalesce(saved, ''), true);
END $f$;

CREATE FUNCTION control.unscanned_audio(p_now timestamptz, p_max_s int) RETURNS TABLE (household_id text, inbox_id text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $f$
DECLARE h record; saved text := current_setting('app.household_id', true);
BEGIN
  FOR h IN SELECT x.id FROM public.households x LOOP
    PERFORM set_config('app.household_id', h.id, true);
    RETURN QUERY SELECT i.household_id, i.id FROM public.inbox i
      WHERE i.kind = 'audio' AND i.received_at + make_interval(secs => p_max_s) < p_now
        AND NOT EXISTS (SELECT 1 FROM public.raw_text t WHERE t.inbox_id = i.id AND t.part = 'transcript');
  END LOOP;
  PERFORM set_config('app.household_id', coalesce(saved, ''), true);
END $f$;

REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA control FROM PUBLIC;
-- EXECUTE grants come before the hand-over: after it the migrator is no longer the owner and cannot grant
GRANT EXECUTE ON FUNCTION control.resolve_sender(text), control.rate_state(timestamptz, text) TO saathi_edge;
GRANT EXECUTE ON FUNCTION control.resolve_sender(text), control.resolve_approver(bigint), control.lease_next(timestamptz, int),
  control.reap_inbox(timestamptz), control.unacked_inbox(timestamptz, int), control.rate_state(timestamptz, text),
  control.due_alerts(timestamptz, int), control.unscanned_audio(timestamptz, int), control.rejected_digest(timestamptz),
  control.audit_append(text, text, text, text, text, text, jsonb) TO saathi_core;
GRANT EXECUTE ON FUNCTION control.audit_append(text, text, text, text, text, text, jsonb), control.rejected_digest(timestamptz) TO saathi_owner;

ALTER FUNCTION control.resolve_sender(text)         OWNER TO saathi_scan;
ALTER FUNCTION control.resolve_approver(bigint)     OWNER TO saathi_scan;
ALTER FUNCTION control.rejected_digest(timestamptz) OWNER TO saathi_scan;
ALTER FUNCTION control.rate_state(timestamptz, text) OWNER TO saathi_scan;
ALTER FUNCTION control.lease_next(timestamptz, int) OWNER TO saathi_scan;
ALTER FUNCTION control.reap_inbox(timestamptz)      OWNER TO saathi_scan;
ALTER FUNCTION control.unacked_inbox(timestamptz, int) OWNER TO saathi_scan;
ALTER FUNCTION control.due_alerts(timestamptz, int) OWNER TO saathi_scan;
ALTER FUNCTION control.unscanned_audio(timestamptz, int) OWNER TO saathi_scan;
ALTER FUNCTION control.audit_append(text, text, text, text, text, text, jsonb) OWNER TO saathi_audit;
REVOKE CREATE ON SCHEMA control FROM saathi_scan, saathi_audit;

-- table privileges the functions need
GRANT SELECT ON households, inbox, alert_events, raw_text, control.household_config TO saathi_scan;
GRANT SELECT ON elder_identities, approvers, rejected_inbound TO saathi_scan;
GRANT UPDATE (state, attempts, lease_until, lease_token) ON inbox TO saathi_scan;
GRANT INSERT, SELECT (seq) ON audit_log TO saathi_audit;
```

```sql
-- appended to 004 after the functions; column lists name what each role may change
-- edge: insert only, plus the public half of the content key
GRANT INSERT ON inbox, raw_text, rejected_inbound, alert_events TO saathi_edge;
GRANT SELECT (household_id, purpose, key_id, public_key, retired_at) ON control.household_keys TO saathi_edge;
GRANT USAGE ON SEQUENCE rejected_inbound_id_seq TO saathi_edge;
-- core: the worker, alert service and caregiver bot
GRANT SELECT ON households, elders, approvers, elder_identities, inbox, raw_text, alert_events, alert_attempts, tg_updates,
  requests, quotes, commits, approvals, addresses, profile_facts, runtime_flags,
  control.signoffs, control.consent_events, control.household_config, control.household_keys TO saathi_core;
GRANT INSERT ON raw_text, alert_events, alert_attempts, tg_updates, requests, quotes, commits, approvals,
  addresses, profile_facts, runtime_flags TO saathi_core;
GRANT UPDATE (state, attempts, lease_until, lease_token, acked_at, radar_at, not_before) ON inbox TO saathi_core;
GRANT UPDATE (state, stage, next_due_at, acked_by, acked_at, false_alarm, blocked_reason, followup_due_at, last_elder_clip_at)
  ON alert_events TO saathi_core;
GRANT UPDATE (status, sent_at, provider_ref_hmac) ON alert_attempts TO saathi_core;
GRANT UPDATE (state, state_reason, questions_asked, urgent, expires_at, superseded_by, slots, quote_id, plan_step, intent,
  confidence, tier_used, radar, trace_id) ON requests TO saathi_core;
GRANT USAGE ON SEQUENCE alert_attempts_id_seq, approvals_id_seq, commits_id_seq, profile_facts_id_seq TO saathi_core;
-- owner: the admin commands of M24, run on the host shell
GRANT SELECT, INSERT ON households, elders, approvers, elder_identities, control.household_config,
  control.consent_events, control.household_keys, control.signoffs TO saathi_owner;
GRANT UPDATE (active) ON elder_identities TO saathi_owner;                    -- elder deactivation
GRANT UPDATE (retired_at, wrapped_key) ON control.household_keys TO saathi_owner;   -- rotation and rewrap
GRANT UPDATE (revoked_at) ON control.signoffs TO saathi_owner;                -- signoff revoke
GRANT USAGE ON SEQUENCE control.signoffs_id_seq, control.consent_events_id_seq TO saathi_owner;
-- readonly: reads every table (it bypasses row-level security by role attribute)
GRANT SELECT ON ALL TABLES IN SCHEMA public, control TO saathi_readonly;
```

## 3. Migration 005 appends

`control.beat` lives here because its table is created in 005. It accepts only the component that matches the login role, so the edge cannot write the core's heartbeat.

```sql
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
```

## 4. Check of module calls against grants

Every call a module makes in the intake and safety parts has a row. A role not named has no grant.

| Call | Module | Granted to |
|---|---|---|
| `resolve_sender`, `rate_state` | M07 edge | edge, core |
| `beat` | M07 edge, M20 worker loop | edge, core |
| `resolve_approver` | M15 | core |
| `lease_next`, `reap_inbox`, `unacked_inbox` | M12 | core |
| `due_alerts`, `unscanned_audio` | M14 | core |
| `rejected_digest` | M12 weekly digest, M24 | core, owner |
| `audit_append` | M17, M24 | core, owner |
| insert `inbox`, `raw_text`, `alert_events`, `rejected_inbound`; read public key | M07 | edge |
| inbox update columns, `raw_text` insert, request and alert writes, `tg_updates` insert | M12, M14, M15 | core |
| `households`, `elders`, `approvers`, `elder_identities` insert; keys, signoffs, consent, config insert; key rotation and rewrap updates; signoff revoke | M24 | owner |
| `llm_reservations`, `llm_spend` | M19 | model |
| `voice_clips` read and review update | M16, M24 | core, owner |

`households` rows: owner and `saathi_scan` read all rows through their own policies, core and readonly read through the tenant policy or the role attribute, and edge and model have no grant. `lease_next` and `unscanned_audio` were run on one row; their behaviour under two concurrent workers rests on `tests/test_lease.py` (LT-09).

## 5. Sequences

Nine tables take a `bigserial` and each role that inserts needs `USAGE` on its sequence. The run reproduced `permission denied for sequence rejected_inbound_id_seq` for the edge before the grant. `audit_log.seq` is an identity column and needs none.

| Sequence | Role |
|---|---|
| `rejected_inbound_id_seq` | edge |
| `alert_attempts_id_seq`, `approvals_id_seq`, `commits_id_seq`, `profile_facts_id_seq` | core |
| `llm_spend_id_seq`, `llm_reservations_id_seq` | model |
| `control.signoffs_id_seq`, `control.consent_events_id_seq` | owner |

`tests/test_roles.py` reads `pg_class` for every sequence and fails on one that no inserting role can use (LT-03).

## 6. Run and limits

On PostgreSQL 16.15 in a scratch instance: the role section ran as the superuser, 001 to 005 with the appends ran as `saathi_migrator`, and 46 assertions passed (hub section 6). The migrator holds no superuser or role-creation right. `INSERT ... ON CONFLICT DO NOTHING` works for the edge with no `SELECT` grant, and the foreign keys check without one.

Limits. `app.household_id` is a setting any session can set: row-level security stops bugs in code, not a compromised role, so a stolen edge or core login can set another household's id. The role split limits what such a login can do, and the Phase 1 service split limits who holds it. The owner can disable row-level security on a table it owns (the migrator); the audit chain of Phase 1 covers that (013). The `saathi_scan` policies read every household by design.

Skipped or not checked: two concurrent workers on `lease_next`; the Phase 1 tables; the init script as a shell file with the real variables; the `vector` extension (absent in the scratch instance); the sequence list is read from the catalog of this run, not of the Phase 1 migrations.
