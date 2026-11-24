#!/usr/bin/env python3
"""Render the same SQLDoom frame on CedarDB and on PostgreSQL, and time both.

Run postgres/clone_to_postgres.py first so both databases hold the same world.
The camera pose comes from the Postgres snapshot's player_state, so both
engines draw exactly the same view of exactly the same data.

    set -a; source .env; set +a          # DB_DSN = CedarDB, PG_DSN = Postgres
    python3 postgres/bench_frame.py [--runs 10] [--jit on|off] [--workers N] [--save frames]

--cedar and --pg override $DB_DSN and $PG_DSN.

--jit and --workers only change the Postgres session (jit,
max_parallel_workers_per_gather), so you can show what each knob does live.
--save writes both frames as PNGs (needs pygame, which the client already uses).
"""
import argparse
import os
import statistics
import sys
import time
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
SQLDOOM = ROOT / "sqldoom"
sys.path.insert(0, str(SQLDOOM))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import doom_sql as sql  # noqa: E402
import port_renderer  # noqa: E402
from doom_config import tune_replanning  # noqa: E402

PARAM_TYPES = ("int", "int", "int", "float8", "float8", "float8", "float8")
W, H = 320, 200


def run(cur, label, text, params, runs):
    t0 = time.perf_counter()
    sql.prepare_statement(cur, "bench_frame", PARAM_TYPES, text)
    sql.execute_prepared(cur, "bench_frame", params)
    frame = bytes(cur.fetchone()[0])
    first = (time.perf_counter() - t0) * 1000
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        sql.execute_prepared(cur, "bench_frame", params)
        cur.fetchone()
        samples.append((time.perf_counter() - t0) * 1000)
    # The second run gets its own column and stays out of the warm figures:
    # CedarDB spends ~1.5 s on it (it looks like a tiered compile).
    warm = samples[1:] or samples
    med = statistics.median(warm)
    print(f"{label:<10} prepare+first {first:9.1f} ms | second {samples[0]:9.1f} ms | "
          f"warm median {med:8.2f} ms ({1000 / med:6.1f} fps) | min {min(warm):8.2f} ms")
    return frame, med


def save_png(frame, path):
    import pygame
    surface = pygame.image.frombuffer(frame, (W, H), "RGB")
    pygame.image.save(surface, str(path))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cedar", default=os.environ.get("DB_DSN"), help="default: $DB_DSN")
    ap.add_argument("--pg", default=os.environ.get("PG_DSN"), help="default: $PG_DSN")
    ap.add_argument("--map", type=int, default=1)
    ap.add_argument("--player", type=int, default=0)
    ap.add_argument("--skill", type=int, default=2)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--jit", choices=("on", "off"))
    ap.add_argument("--workers", type=int, help="Postgres max_parallel_workers_per_gather")
    ap.add_argument("--save", help="directory to write cedar.png and postgres.png into")
    args = ap.parse_args()
    if not args.cedar or not args.pg:
        ap.error("set DB_DSN and PG_DSN (set -a; source .env; set +a) or pass --cedar/--pg")

    original = sql.load_sql("renderer.sql").rstrip().rstrip(";")
    ported = port_renderer.port(original)

    pg = psycopg2.connect(args.pg)
    pg.autocommit = True
    pc = pg.cursor()
    # Plan once and reuse, like the game client does with PREPARE on CedarDB.
    # Without this Postgres re-plans the 1,600-line query on each of the
    # first five executions ("custom plans").
    pc.execute("SET plan_cache_mode = force_generic_plan")
    if args.jit:
        pc.execute(f"SET jit = {args.jit}")
    if args.workers is not None:
        pc.execute(f"SET max_parallel_workers_per_gather = {args.workers:d}")
    pc.execute("SELECT version(), current_setting('jit'), "
               "current_setting('max_parallel_workers_per_gather')")
    version, jit, workers = pc.fetchone()
    pc.execute("SELECT position_x, position_y, view_z, view_angle FROM player_state "
               "WHERE map_id = %s AND player_thing_id = %s", (args.map, args.player))
    pose = pc.fetchone()
    if pose is None:
        sys.exit("no player_state row in the Postgres copy -- play once, then re-clone")
    params = (args.map, args.player, args.skill, *map(float, pose))

    cedar = psycopg2.connect(args.cedar)
    cedar.autocommit = True
    cc = cedar.cursor()
    tune_replanning(cc)

    print(version.split(",")[0], f"| jit={jit} parallel_workers_per_gather={workers}")
    print(f"pose x={pose[0]:.0f} y={pose[1]:.0f} z={pose[2]:.0f} angle={pose[3]:.0f}\n")

    f_cedar, t_cedar = run(cc, "CedarDB", original, params, args.runs)
    f_pg, t_pg = run(pc, "Postgres", ported, params, args.runs)

    diff = sum(1 for i in range(0, min(len(f_cedar), len(f_pg)), 3)
               if f_cedar[i:i + 3] != f_pg[i:i + 3])
    print(f"\nPostgres / CedarDB warm time: {t_pg / t_cedar:.1f}x")
    print(f"pixels that differ: {diff} of {W * H}"
          + ("" if diff == 0 else "  (a handful is float rounding; lots means a port bug)"))
    if args.save:
        out = Path(args.save)
        out.mkdir(parents=True, exist_ok=True)
        save_png(f_cedar, out / "cedar.png")
        save_png(f_pg, out / "postgres.png")
        print(f"frames written to {out}/")


if __name__ == "__main__":
    main()
