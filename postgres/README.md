**NOTE**: This work has no affilation with CedarDB or the origional author. This README.md and the rest of the code in PostgreSQL is AI-generated. See [AI-Disclosure.md].

# SQLDoom on stock PostgreSQL

Runs the same renderer query (`sqldoom/sql/renderer.sql`) on PostgreSQL, against a copy
of the same world, so the two engines can be compared directly. Only the
renderer is ported; the game tic is cedarscript and stays on CedarDB.

PostgreSQL runs as a service in `docker-compose.yml`, in the same
Docker VM as CedarDB, so both engines get the same CPUs and memory.

```bash
docker compose up -d                       # or docker-compose; 127.0.0.1:5433, data in postgres/pgdata
set -a; source .env; set +a                # DB_DSN = loaded CedarDB, PG_DSN = Postgres
python postgres/clone_to_postgres.py       # copy the world (about 3 s)
python postgres/bench_frame.py --save postgres/frames
python postgres/bench_frame.py --jit off --workers 0
python postgres/flythrough.py              # 360° spin on both, ~1 min, writes postgres/flythrough/flythrough.html
```

`.env` needs the `PG_*` block from `.env-template`. Re-run the clone whenever
you want Postgres to see a new player pose. Stop Postgres with
`docker compose stop postgres`.

| file | what it does |
| --- | --- |
| `compat.sql` | `arg_max` aggregate shim (CedarDB has it built in) |
| `port_renderer.py` | textual patches that turn `renderer.sql` into Postgres SQL |
| `clone_to_postgres.py` | copies every table in `sqldoom/sql/schema.sql` from CedarDB to Postgres |
| `bench_frame.py` | renders one frame on both, times it, diffs the pixels |
| `flythrough.py` | renders a 360° spin on both and writes an offline HTML player that replays each engine on its real timeline |
| `explain_renderer.py` | saves the renderer's plan from both engines as text, JSON and side-by-side SVG posters |

## Fly-through page

`flythrough.py` turns the camera in place from the player's pose (36 frames,
10° apart by default) and renders every pose on each engine, CedarDB first and
then Postgres. The HTML it writes embeds every frame (~3.5 MB), so it works
offline:

- **Play in real time:** each side shows the newest frame it had finished at
  that moment. CedarDB finishes its spin in ~0.7 s while Postgres is still on
  its first frame; it takes ~56 s to finish. Use the speed menu to slow
  CedarDB down or fast-forward Postgres.
- **Same pose on both:** the slider puts both sides on the same angle and shows
  the pixel difference (0 everywhere).

Postgres defaults to `--jit off` here; `--jit on` takes ~12 s per frame, so a
36-frame spin takes about 7 minutes.

## Seeing the renderer's plan on both engines

```bash
python postgres/explain_renderer.py        # ~5 s; writes postgres/plans/
```

For the same frame, it writes `cedar_plan.*` and `postgres_plan.*`: the
`EXPLAIN ANALYZE` text, the JSON, and an SVG poster drawn with
`sqldoom/scripts/plan_poster.py`'s layout. Postgres node types are mapped onto
plan_poster's colour categories, so colours mean the same thing on both.
`plans/index.html` shows the two posters side by side. Postgres is explained
the way the benchmark runs it: prepared, generic plan, `jit = off`.

What the two plans have in common, and where they differ (E1M1 start pose):

| | CedarDB | PostgreSQL |
| --- | --- | --- |
| operators | 598 | 538 |
| joins | 180 | 180 |
| reads of materialized intermediate results | 178 | 193 (CTE Scan, Subquery Scan) |
| sorts | 4 | 16 |
| separate filter / projection operators | 50 / 41 | 4 / 3 (folded into scans and joins) |
| depth drawn | 80 | 31 |

The plans are broadly the same shape, which is the point: the ~80x gap is how
each engine executes a plan, not which plan it picks. The depth differs mostly
because Postgres attaches each materialized CTE to the top of the plan, while
CedarDB draws it where it is used.

## CedarDB's second run

Right after PREPARE, CedarDB's executions go ~200 ms, then ~1.6 s, then ~20 ms
from the third run on, reproducibly. It looks like tiered compilation: a quick
plan for the first run, a fully optimized compile paid once on the second.
Postgres is the opposite: with JIT on it recompiles (~10 s) on every run.
`bench_frame.py` prints the second run in its own column and leaves it out of
the warm figures; `flythrough.py` runs two warm-ups first and shows their times
on the page.

## Measured baseline

E1M1 start pose, M-series Mac, Docker VM with 6 CPUs / 8 GB shared by both
containers, PostgreSQL 18.7 (`postgres:18`), 2026-11-23. Warm frame = median of
5 executions of the prepared statement.

| engine / setting | warm frame | fps | vs CedarDB |
| --- | --- | --- | --- |
| CedarDB | ~20 ms | ~50 | 1x |
| Postgres, defaults (`jit = on`) | ~11.8 s | 0.1 | ~580x |
| Postgres, `jit = off` | ~1.69 s | 0.6 | ~82x |
| Postgres, `jit = off`, 0 parallel workers | ~1.68 s | 0.6 | ~86x |

Pixels that differ: 0 of 64,000 in every run.

What `EXPLAIN ANALYZE` shows on the Postgres side:

- **JIT costs more than it saves.** The planner's cost estimate (~3.4M) is far
  above `jit_optimize_above_cost` and `jit_inline_above_cost` (500k), so
  Postgres compiles 1,122 functions with full LLVM optimization: about 4.7 s
  optimizing plus 5.2 s emitting code, ~10 s of the ~11.6 s total. Postgres
  does not keep JIT code between executions, so a prepared statement pays it
  again on every frame. CedarDB compiles once, at prepare, in ~200 ms.
- **No parallelism.** The plan has 161 CTE Scan nodes and no Gather node, so
  `max_parallel_workers_per_gather` changes nothing.
- **Planning** takes ~90 ms for the 89 CTEs.

## OLAP demo table

`olap.tic_log` is synthetic gameplay telemetry: one row per player per game
tic, 20 columns, 64 players at 35 tics a second. It joins to the real
`sectors` table from the WAD, so it is a small star schema. Every value is
arithmetic on the row number, so both engines hold identical rows and must
return identical answers. It lives in schema `olap`, which the game and
`clone_to_postgres.py` never touch.

| file | what it does |
| --- | --- |
| `olap/tic_log.sql` | creates and fills the table (`:rows` placeholder; runs in psql with `-v rows=N`) |
| `olap/queries.sql` | the five demo queries, each under a `-- name:` line (`-- repeat: N` runs one N times with different keys) |
| `olap.py` | `load` / `drop` / `bench` on both engines |

```bash
python postgres/olap.py drop
python postgres/olap.py load --rows 10000000     # CedarDB ~8 s, Postgres ~1.5-2.5 min
python postgres/olap.py bench --jit off --only Q1
python postgres/olap.py bench --jit off --only Q4
```

Measured at 10M rows, Postgres `jit = off`, same Docker VM:

| query | CedarDB | PostgreSQL | |
| --- | --- | --- | --- |
| load (INSERT ... SELECT generate_series) | 7.6 s | 112 s | 15x |
| Q1 narrow scan: avg of 2 of 20 columns | 20 ms | 488 ms | 25x |
| Q2 star join + group by | 61 ms | 957 ms | 16x |
| Q3 window (lag over each player) | 3.6 s | 105 s | 29x |
| Q4 point lookup by primary key | 0.8 ms | 0.4 ms | **Postgres 1.8x** |
| Q5 1,000 one-off lookups, one query each | 813 ms | 450 ms | **Postgres 1.8x** |

All five answers match. Notes:

- Q5 is the OLTP counterpoint. Per statement (1,000 in a loop, same Docker VM):

  | | CedarDB | Postgres |
  | --- | --- | --- |
  | `SELECT 1` (round-trip floor) | 0.50 ms | 0.35 ms |
  | prepared lookup | 0.49 ms | 0.39 ms |
  | one-off lookup, literal keys (Q5) | 0.65-0.81 ms | 0.39-0.45 ms |

  Postgres wins on fixed cost per statement: parsing, planning and
  interpreting a one-row plan cost it almost nothing beyond the round trip.
  CedarDB spends ~0.15 ms more on each new query text, and its baseline per
  statement is higher too. Compilation pays off over millions of rows and costs
  time over one.
- Single-row UPDATEs (1,000 autocommit transactions, not in queries.sql)
  went the other way: CedarDB 0.47 ms each, Postgres 0.57 ms.

- Q1 on Postgres touches 181,819 pages (1.4 GB, `EXPLAIN (ANALYZE, BUFFERS)`)
  to average two smallint columns, which are 40 MB of actual data.
- Q3 takes minutes on Postgres with the default 3 runs; use `--runs 1`.
- Postgres parallel workers do help these scans (Q1 ~800 ms with 0 workers vs
  ~380-490 ms with 2), unlike the renderer, which never gets a parallel plan.
- `jit = on` made Postgres slower here too (Q1 ~610 ms vs ~380 ms in the same run).
- Q3 casts to float8 on purpose: CedarDB keeps `sqrt`/`sum` of `real` in
  4-byte floats while Postgres promotes to double, and the totals drift ~2,000
  apart. A small dialect difference worth knowing about.
