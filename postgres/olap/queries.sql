-- OLAP queries over olap.tic_log (see tic_log.sql). Each starts with a
-- "-- name:" line, which postgres/olap.py uses to label and time it. They also
-- run as they are in psql against either engine.

-- name: Q1 narrow scan -- average of 2 of 20 columns
-- A column store reads only health and armor; a row store reads every row whole.
SELECT avg(health) AS avg_health, avg(armor) AS avg_armor
FROM olap.tic_log;

-- name: Q2 star join -- player-hours per floor texture
-- Fact table joined to a dimension (the real sectors from the WAD), then grouped.
SELECT s.floor_tex,
       round(count(*) / 35.0 / 3600, 1) AS player_hours,
       round(avg(t.health), 2)          AS avg_health
FROM olap.tic_log t
JOIN sectors s ON s.map_id = t.map_id AND s.id = t.sector_id
GROUP BY s.floor_tex
ORDER BY player_hours DESC, s.floor_tex
LIMIT 5;

-- name: Q3 window -- distance travelled per player
-- lag() over each player's tics: sorts (or partitions) all rows.
-- The float8 casts matter: x and y are real (4-byte). PostgreSQL has no
-- sqrt(real), so it quietly promotes to double; CedarDB keeps sqrt and sum in
-- real, and over 150k rows per player the totals drift apart by ~2,000.
-- In double precision both agree to the integer.
SELECT player_id, round(sum(sqrt(dx * dx + dy * dy))::numeric, 0) AS distance
FROM (
  SELECT player_id,
         x::float8 - lag(x::float8) OVER w AS dx,
         y::float8 - lag(y::float8) OVER w AS dy
  FROM olap.tic_log
  WINDOW w AS (PARTITION BY player_id ORDER BY tic)
) d
GROUP BY player_id
ORDER BY distance DESC, player_id
LIMIT 3;

-- name: Q4 point lookup (OLTP) -- one row by primary key
-- The counterpoint: the kind of query a row store and a B-tree are built for.
SELECT health, weapon, x, y
FROM olap.tic_log
WHERE player_id = 7 AND tic = 1000;

-- name: Q5 OLTP throughput -- 1,000 one-row lookups, each its own query
-- repeat: 1000
-- olap.py sends this 1,000 times with different literal keys, one round trip
-- and one fresh query text each, like an application looking up records.
-- Each lookup is tiny, so what decides the race is the fixed cost of every
-- statement: parsing, planning and (for CedarDB) compiling, plus the round
-- trip. Postgres interprets a tiny plan almost for free; compiling costs time
-- that 1 row can never pay back. In psql: \set player_id 7  \set tic 1000
SELECT health, weapon, x, y
FROM olap.tic_log
WHERE player_id = :player_id AND tic = :tic;
