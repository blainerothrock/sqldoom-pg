#!/usr/bin/env python3
"""Copy a loaded SQLDoom database from CedarDB into stock PostgreSQL.

Load the WAD into CedarDB as usual first (wad_loader.py). The loader itself
needs CedarDB because it runs cedarscript, so this copies the finished tables
instead of re-importing:

    docker compose up -d postgres
    set -a; source .env; set +a          # DB_DSN = CedarDB, PG_DSN = Postgres
    python3 postgres/clone_to_postgres.py

What it does on the target:
  1. applies sql/schema.sql (it is plain PostgreSQL),
  2. installs the renderer's light functions and the ARG_MAX shim,
  3. streams every non-empty table across with COPY,
  4. refreshes the materialized views and runs ANALYZE.

Only the renderer is ported. The game tic is cedarscript and stays on CedarDB,
so the Postgres copy is a frozen snapshot of the world at copy time: close
the game or stand still while it runs, then compare frames.
"""
import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent

# Refresh order: ui_static_pixels reads tables only, but keep the loader's order.
MATVIEWS = ("linedef_geom", "sector_adjacency", "node_path_steps",
            "ui_hud_pixels", "ui_static_pixels")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=os.environ.get("DB_DSN"),
                    help="CedarDB DSN, already loaded (default: $DB_DSN)")
    ap.add_argument("--target", default=os.environ.get("PG_DSN"),
                    help="PostgreSQL DSN, will be overwritten (default: $PG_DSN)")
    args = ap.parse_args()
    if not args.source or not args.target:
        ap.error("set DB_DSN and PG_DSN (set -a; source .env; set +a) or pass --source/--target")

    src = psycopg2.connect(args.source)
    src.autocommit = True
    dst = psycopg2.connect(args.target)
    dst.autocommit = True
    s, d = src.cursor(), dst.cursor()

    print("target: dropping and recreating schema public")
    d.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
    d.execute((ROOT / "sqldoom" / "sql" / "schema.sql").read_text(encoding="utf-8"))
    d.execute((ROOT / "sqldoom" / "sql" / "runtime" / "functions" / "43_render_light.sql").read_text(encoding="utf-8"))
    d.execute((HERE / "compat.sql").read_text(encoding="utf-8"))

    # The table list comes from the target, which only has what schema.sql
    # defines, so CedarDB's own catalog layout doesn't matter.
    d.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    tables = [r[0] for r in d.fetchall()]

    # Tables go across alphabetically, not in foreign-key order. The source is
    # already consistent, so skip the FK triggers during the bulk load (this
    # needs superuser, which the local demo database has).
    d.execute("SET session_replication_role = replica")

    total_rows = 0
    started = time.perf_counter()
    for table in tables:
        d.execute(
            "SELECT string_agg(quote_ident(column_name), ',' ORDER BY ordinal_position) "
            "FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
            (table,))
        cols = d.fetchone()[0]
        s.execute(f"SELECT count(*) FROM {table}")
        n = s.fetchone()[0]
        if n == 0:
            continue
        with tempfile.TemporaryFile() as buf:
            s.copy_expert(f"COPY (SELECT {cols} FROM {table}) TO STDOUT", buf)
            buf.seek(0)
            d.copy_expert(f"COPY {table} ({cols}) FROM STDIN", buf)
        total_rows += n
        print(f"  {table:<32}{n:>10,} rows")

    d.execute("RESET session_replication_role")
    for view in MATVIEWS:
        d.execute(f"REFRESH MATERIALIZED VIEW {view}")
    d.execute("ANALYZE")
    print(f"copied {total_rows:,} rows in {time.perf_counter() - started:.1f} s; "
          f"materialized views refreshed, statistics gathered")


if __name__ == "__main__":
    sys.exit(main())
