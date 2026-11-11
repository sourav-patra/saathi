-- 006_ledger_global.sql (RQ-48; WU-21 review ruling 4): one hard cap across households. Runs as saathi_migrator.
-- The model role sees one household's reservations (row security), so the global month total comes from a
-- security-definer function that returns one number. It sets the household per row, as the other looping functions
-- do, and gives the caller's setting back; no new row policy is needed.
GRANT SELECT ON llm_reservations TO saathi_scan;
GRANT CREATE ON SCHEMA control TO saathi_scan;      -- only so ALTER FUNCTION ... OWNER TO works; revoked below
CREATE FUNCTION control.ledger_month_total(p_month text) RETURNS numeric
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $f$
DECLARE h record; total numeric := 0; part numeric; saved text := current_setting('app.household_id', true);
BEGIN
  FOR h IN SELECT x.id FROM public.households x LOOP
    PERFORM set_config('app.household_id', h.id, true);
    SELECT COALESCE(SUM(CASE r.state WHEN 'reserved' THEN r.reserved_inr WHEN 'settled' THEN r.settled_inr ELSE 0 END), 0)
      INTO part FROM public.llm_reservations r WHERE r.month = p_month;
    total := total + part;
  END LOOP;
  PERFORM set_config('app.household_id', coalesce(saved, ''), true);
  RETURN total;
END $f$;
REVOKE EXECUTE ON FUNCTION control.ledger_month_total(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.ledger_month_total(text) TO saathi_model;
GRANT USAGE ON SCHEMA control TO saathi_model;      -- to reach the function; no table of the schema is granted
ALTER FUNCTION control.ledger_month_total(text) OWNER TO saathi_scan;
REVOKE CREATE ON SCHEMA control FROM saathi_scan;
