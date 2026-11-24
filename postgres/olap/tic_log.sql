-- olap.tic_log: synthetic SQLDoom gameplay telemetry, for OLAP demos.
--
-- One row = one player's state at one game tic. 64 players, 35 tics a second,
-- so 10M rows is about 43 minutes of a 64-player match.
--
-- It is a star schema: tic_log is the fact table; the real level data loaded
-- from the WAD (sectors, maps) are the dimensions.
--
-- Nothing is random. Every value is arithmetic on the row number i (h is a
-- multiplicative hash of i), so CedarDB and PostgreSQL hold exactly the same
-- rows and every query must return the same answer on both.
--
-- Load with postgres/olap.py, or by hand with psql:
--     psql "<dsn>" -v rows=10000000 -f postgres/olap/tic_log.sql

CREATE SCHEMA IF NOT EXISTS olap;

DROP TABLE IF EXISTS olap.tic_log;

-- 20 columns on purpose: a query that reads 2 of them is where a column store
-- (CedarDB) and a row store (PostgreSQL) behave very differently.
CREATE TABLE olap.tic_log (
  tic         bigint   NOT NULL,  -- game time, 35 per second
  player_id   int      NOT NULL,  -- 0..63
  map_id      int      NOT NULL,  -- -> maps, sectors
  sector_id   int      NOT NULL,  -- -> sectors(map_id, id): where the player stands
  x           real,
  y           real,
  z           real,
  angle       real,
  health      smallint,
  armor       smallint,
  weapon      smallint,
  bullets     smallint,
  shells      smallint,
  rockets     smallint,
  cells       smallint,
  kills       int,
  items       int,
  secrets     int,
  firing      boolean,
  client_info text,               -- ~50 chars, almost the same on every row
  PRIMARY KEY (player_id, tic)
);

INSERT INTO olap.tic_log
SELECT
  g.i / 64                                            AS tic,
  (g.i % 64)::int                                     AS player_id,
  m.map_id,
  -- a real sector of that map; squaring skews the choice so a few
  -- sectors are hot spots, like a real level's main rooms
  (m.lo + ((g.h % m.n) * (g.h % m.n)) / m.n)::int     AS sector_id,
  ((g.h % 4000) - 2000)::real                         AS x,
  ((g.h / 4000 % 4000) - 2000)::real                  AS y,
  (g.h % 97)::real                                    AS z,
  (g.h % 360)::real                                   AS angle,
  (100 - (g.h / 13) % 100)::smallint                  AS health,
  ((g.h / 17) % 201)::smallint                        AS armor,
  ((g.h / 19) % 8)::smallint                          AS weapon,
  ((g.h / 23) % 201)::smallint                        AS bullets,
  ((g.h / 29) % 51)::smallint                         AS shells,
  ((g.h / 31) % 51)::smallint                         AS rockets,
  ((g.h / 37) % 301)::smallint                        AS cells,
  ((g.i / 64) / 700)::int                             AS kills,    -- one every 20 s
  ((g.i / 64) / 2100)::int                            AS items,    -- one a minute
  ((g.i / 64) / 21000)::int                           AS secrets,  -- one every 10 min
  (g.h % 5) = 0                                       AS firing,   -- 1 tic in 5
  'sqldoom-client/1.0 python3.13 pygame-2.6.1 seat-' || (g.i % 64)::text AS client_info
FROM (
  SELECT i,
         (i * 2654435761) % 4294967296            AS h,       -- Knuth's multiplicative hash
         1 + ((i % 64) + (i / 64) / 21000) % 9    AS map_no   -- next map every 10 minutes
  FROM generate_series(0, :rows - 1) AS s(i)
) g
JOIN (SELECT map_id, count(*) AS n, min(id) AS lo FROM sectors GROUP BY map_id) m
  ON m.map_id = g.map_no;

-- Fresh statistics, so the optimizer knows the table is big.
ANALYZE olap.tic_log;
