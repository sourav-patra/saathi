-- 007_due_followups.sql (RQ-18; WU-17 review F3): control.due_alerts also returns an acknowledged event whose 15 minute
-- follow-up is due (`followup_due_at` has passed). The service sets the column to NULL once the follow-up is out, so
-- the scan needs no table beyond the ones it already reads. No grant changes. Runs as saathi_migrator.
-- 004 is applied and is not edited: the function is replaced here and keeps its owner and its grants.
GRANT CREATE ON SCHEMA control TO saathi_scan;      -- only so the owner can replace its function; revoked below
SET LOCAL ROLE saathi_scan;                           -- the migrator may SET to it (see the init script)
CREATE OR REPLACE FUNCTION control.due_alerts(p_now timestamptz, p_limit int) RETURNS TABLE (household_id text, event_id text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $f$
DECLARE h record; saved text := current_setting('app.household_id', true);
BEGIN
  FOR h IN SELECT x.id FROM public.households x LOOP
    PERFORM set_config('app.household_id', h.id, true);
    RETURN QUERY SELECT a.household_id, a.id FROM public.alert_events a
      WHERE (a.state = 'open' AND a.next_due_at <= p_now)
         OR (a.state = 'acknowledged' AND a.followup_due_at <= p_now)
      LIMIT p_limit;
  END LOOP;
  PERFORM set_config('app.household_id', coalesce(saved, ''), true);
END $f$;
RESET ROLE;
REVOKE CREATE ON SCHEMA control FROM saathi_scan;
