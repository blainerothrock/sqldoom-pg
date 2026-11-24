#!/usr/bin/env python3
"""Load, drop, and benchmark the OLAP demo table (olap.tic_log) on both engines.

The SQL lives in postgres/olap/: tic_log.sql builds the table, queries.sql holds
the queries. Both are meant to be shown on screen; this script only runs them.

    set -a; source .env; set +a          # DB_DSN = CedarDB, PG_DSN = Postgres
    python3 postgres/olap.py load [--rows 10000000] [--engine both|cedar|pg]
    python3 postgres/olap.py bench [--runs 3] [--jit on|off] [--workers N] [--only Q2]
    python3 postgres/olap.py drop  [--engine both|cedar|pg]

The table lives in its own schema, so the game never touches it, and
clone_to_postgres.py (which rebuilds only schema public) leaves it alone.
"""
import argparse
import os
import re
import statistics
import sys
import time
from pathlib import Path

import psycopg2

HERE = Path(__file__).resolve().parent / "olap"


def connect(dsn, sets=()):
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    for s in sets:
        cur.execute(s)
    return cur


def statements(path, rows=None):
    """Split a SQL file on statement-ending semicolons and fill in :rows."""
    text = path.read_text(encoding="utf-8")
    if rows is not None:
        text = text.replace(":rows", str(rows))
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))
    return [s.strip() for s in re.split(r";\s*\n", body) if s.strip()]


def queries():
    """(name, sql) pairs from queries.sql, split on its '-- name:' lines."""
    parts = re.split(r"^-- name: (.+)$", (HERE / "queries.sql").read_text(encoding="utf-8"), flags=re.M)
    return [(parts[i].strip(), parts[i + 1].strip().rstrip(";")) for i in range(1, len(parts), 2)]


def lookup_keys(cur, n):
    """n deterministic (player_id, tic) keys spread over the loaded table.

    The last tic is read through the primary key (player_id, tic), so this is
    an index lookup on both engines, not a scan.
    """
    cur.execute("SELECT tic FROM olap.tic_log WHERE player_id = 0 ORDER BY tic DESC LIMIT 1")
    max_tic = cur.fetchone()[0] + 1
    return [((i * 37) % 64, (i * 7919) % max_tic) for i in range(n)]


def engines(args, pg_sets=()):
    picked = []
    if args.engine in ("both", "cedar"):
        picked.append(("CedarDB", os.environ.get("DB_DSN"), ()))
    if args.engine in ("both", "pg"):
        picked.append(("PostgreSQL", os.environ.get("PG_DSN"), tuple(pg_sets)))
    for name, dsn, _ in picked:
        if not dsn:
            sys.exit(f"no DSN for {name}: set -a; source .env; set +a")
    return picked


def short(stmt):
    return " ".join(stmt.split())[:60]


def cmd_load(args):
    for name, dsn, _ in engines(args):
        cur = connect(dsn)
        print(f"{name}: loading {args.rows:,} rows")
        started = time.perf_counter()
        for stmt in statements(HERE / "tic_log.sql", args.rows):
            t0 = time.perf_counter()
            cur.execute(stmt)
            print(f"  {time.perf_counter() - t0:8.2f} s  {short(stmt)}")
        cur.execute("SELECT count(*) FROM olap.tic_log")
        print(f"  {cur.fetchone()[0]:,} rows in {time.perf_counter() - started:.1f} s")
        if name == "PostgreSQL":
            cur.execute("SELECT pg_size_pretty(pg_table_size('olap.tic_log')), "
                        "pg_size_pretty(pg_indexes_size('olap.tic_log'))")
            table, index = cur.fetchone()
            print(f"  on disk: table {table}, primary key index {index}")
        print()


def cmd_drop(args):
    for name, dsn, _ in engines(args):
        connect(dsn).execute("DROP TABLE IF EXISTS olap.tic_log")
        print(f"{name}: dropped olap.tic_log")


def cmd_bench(args):
    pg_sets = []
    if args.jit:
        pg_sets.append(f"SET jit = {args.jit}")
    if args.workers is not None:
        pg_sets.append(f"SET max_parallel_workers_per_gather = {args.workers:d}")
    picked = engines(args, pg_sets)
    if pg_sets:
        print("PostgreSQL session: " + "; ".join(pg_sets) + "\n")
    for qname, sql in queries():
        if args.only and not qname.startswith(args.only):
            continue
        print(qname)
        # "-- repeat: N" queries run N times per sample, each with its own literal keys.
        m = re.search(r"^-- repeat: (\d+)", sql, flags=re.M)
        repeat = int(m.group(1)) if m else 0
        answers = {}
        times = {}
        for name, dsn, sets in picked:
            cur = connect(dsn, sets)
            if repeat:
                texts = [sql.replace(":player_id", str(p)).replace(":tic", str(t))
                         for p, t in lookup_keys(cur, repeat)]
            else:
                texts = [sql]

            def run_once():
                rows = []
                for text in texts:
                    cur.execute(text)
                    rows.extend(cur.fetchall())
                return rows

            answers[name] = run_once()  # warm-up; also the answer we compare
            samples = []
            for _ in range(args.runs):
                t0 = time.perf_counter()
                run_once()
                samples.append((time.perf_counter() - t0) * 1000)
            times[name] = statistics.median(samples)
            cur.connection.close()
            per = f"  ({times[name] / repeat:.3f} ms each, {repeat * 1000 / times[name]:,.0f}/s)" if repeat else ""
            print(f"  {name:<11} {times[name]:10.1f} ms{per}")
        if len(times) == 2:
            ratio = times["PostgreSQL"] / times["CedarDB"]
            faster = "CedarDB" if ratio >= 1 else "PostgreSQL"
            print(f"  {faster} faster by {max(ratio, 1 / ratio):.1f}x | same answer: "
                  f"{answers['CedarDB'] == answers['PostgreSQL']}")
        first = next(iter(answers.values()))
        print(f"  answer: {first[:3]}{' ...' if len(first) > 3 else ''}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("load", "drop", "bench"):
        p = sub.add_parser(name)
        p.add_argument("--engine", choices=("both", "cedar", "pg"), default="both")
        if name == "load":
            p.add_argument("--rows", type=int, default=10_000_000)
        if name == "bench":
            p.add_argument("--runs", type=int, default=3)
            p.add_argument("--jit", choices=("on", "off"), help="PostgreSQL jit for this run")
            p.add_argument("--workers", type=int, help="PostgreSQL max_parallel_workers_per_gather")
            p.add_argument("--only", help="run only queries whose name starts with this, e.g. Q2")
    args = ap.parse_args()
    {"load": cmd_load, "drop": cmd_drop, "bench": cmd_bench}[args.cmd](args)


if __name__ == "__main__":
    main()
