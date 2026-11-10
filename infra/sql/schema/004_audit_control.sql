-- 004_audit_control.sql (RQ-56a, 95a, 110, 111a; PL-05, 33, 34): LLD-saathi-data section 2 verbatim, then the
-- policies, functions, grants and sequence grants of LLD-saathi-grants section 2, verbatim. Runs as saathi_migrator.
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

-- LLD-saathi-grants section 2: policies, functions, grants
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
