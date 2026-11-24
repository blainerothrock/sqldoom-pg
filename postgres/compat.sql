-- Shims so stock PostgreSQL can run sql/renderer.sql.
-- CedarDB has a built-in ARG_MAX(value, key): the value from the row with the
-- largest key. The renderer uses it once (ui_resolved) on small integers.
CREATE OR REPLACE FUNCTION arg_max_step(state bigint[], val bigint, k bigint)
RETURNS bigint[] LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN k IS NULL THEN state
              WHEN state IS NULL OR k > state[2] THEN ARRAY[val, k]
              ELSE state END
$$;
CREATE OR REPLACE FUNCTION arg_max_final(state bigint[])
RETURNS bigint LANGUAGE sql IMMUTABLE AS $$ SELECT state[1] $$;
DROP AGGREGATE IF EXISTS arg_max(bigint, bigint);
CREATE AGGREGATE arg_max(bigint, bigint) (
  SFUNC = arg_max_step, STYPE = bigint[], FINALFUNC = arg_max_final,
  PARALLEL = SAFE
);
