-- 008_ride_places.sql (ride helper): the saved places of a household reuse table `addresses` (001), which already has
-- tenant row-level security (002). Runs as saathi_migrator.
--   aliases       other names for the place, as the household says them (matched as whole words)
--   is_pickup     the one place a ride starts from (at most one per household)
--   ride_count    rides handed to a caregiver for this place; orders the choice buttons
--   last_ride_at  when the latest one was handed over
ALTER TABLE addresses
  ADD COLUMN aliases      text[]      NOT NULL DEFAULT '{}',
  ADD COLUMN is_pickup    boolean     NOT NULL DEFAULT false,
  ADD COLUMN ride_count   integer     NOT NULL DEFAULT 0 CHECK (ride_count >= 0),
  ADD COLUMN last_ride_at timestamptz;
CREATE UNIQUE INDEX one_pickup_per_household ON addresses (household_id) WHERE is_pickup;

-- grants: the worker and bot (core) already read places (004); they may change only the two counters.
-- The owner's admin commands add, list, alias and remove places; an alias and the pickup flag are the only columns
-- they change afterwards.
GRANT UPDATE (ride_count, last_ride_at) ON addresses TO saathi_core;
REVOKE INSERT ON addresses FROM saathi_core;
GRANT SELECT, INSERT, DELETE ON addresses TO saathi_owner;
GRANT UPDATE (aliases, is_pickup) ON addresses TO saathi_owner;

-- Immutable claims: a callback keeps its card for edit retries; an inbox counts once across caregiver deliveries.
-- No address foreign key: removing a saved place must not discard an already-counted callback's retry card.
CREATE TABLE ride_counts (
  household_id text NOT NULL REFERENCES households(id),
  source text NOT NULL CHECK (source IN ('callback', 'inbox')),
  source_id text NOT NULL,
  place_id text NOT NULL,
  card jsonb,
  PRIMARY KEY (household_id, source, source_id),
  CHECK ((source = 'callback' AND card IS NOT NULL) OR (source = 'inbox' AND card IS NULL))
);
SELECT control.apply_tenant_rls('ride_counts'::regclass, true);
GRANT SELECT, INSERT ON ride_counts TO saathi_core;
GRANT SELECT ON ride_counts TO saathi_readonly;
