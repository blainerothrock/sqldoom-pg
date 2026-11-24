#!/usr/bin/env python3
"""Run the renderer query once, by hand, and look at what it returns.

sql/renderer.sql returns one row with one column, frame_rgb: a single bytea of
320 x 200 x 3 = 192,000 bytes, the whole screen as packed RGB, left to right,
top to bottom. This script runs it from the player's current pose and shows
that value three ways.

    set -a; source .env; set +a          # DB_DSN = CedarDB, PG_DSN = Postgres
    python3 postgres/render_one.py                     # size, time, first pixels
    python3 postgres/render_one.py --png frame.png     # save it as an image
    python3 postgres/render_one.py --ascii             # draw it as text, in SQL
    python3 postgres/render_one.py --sql render_now.sql  # the query, ready for DBeaver/psql
    python3 postgres/render_one.py --engine pg --png pg.png   # same frame on Postgres

--sql writes the query in --engine's dialect: the CedarDB text does not run on
Postgres (see port_renderer.py), so add --engine pg for a Postgres copy.

The query normally takes seven parameters ($1-$7: map, player, skill, x, y, z,
angle). Here map, player and skill become literals and the pose is read from
player_state, so the text runs as is in any SQL client. --ascii wraps that
query in a second SELECT that turns the bytes into characters with get_byte and
string_agg, so the picture is drawn by the database too.
"""
import argparse
import os
import sys
import time
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
# SQLDoom is either this repo itself or a submodule checked out at sqldoom/.
SQLDOOM = next((d for d in (ROOT, ROOT / "sqldoom")
                if (d / "sql" / "renderer.sql").exists()), ROOT / "sqldoom")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import port_renderer  # noqa: E402

W, H = 320, 200
RAMP = " .:-=+*#%@"  # dark to bright


def standalone(map_id, player_id, skill, engine):
    """renderer.sql with its seven parameters filled in from player_state."""
    text = (SQLDOOM / "sql" / "renderer.sql").read_text(encoding="utf-8")
    if engine == "pg":
        text = port_renderer.port(text)
    pose = ("SELECT $4::float8 AS x, $5::float8 AS y, $6::float8 AS z, "
            "$7::float8 AS angle")
    swaps = [
        (pose, "SELECT position_x::float8 AS x, position_y::float8 AS y, "
               "view_z::float8 AS z, view_angle::float8 AS angle "
               f"FROM player_state WHERE map_id = {map_id} "
               f"AND player_thing_id = {player_id}"),
        ("$1::int", str(map_id)),
        ("$2::int", str(player_id)),
        ("$3::int", str(skill)),
    ]
    for old, new in swaps:
        if old not in text:
            sys.exit(f"renderer.sql changed: {old!r} not found")
        text = text.replace(old, new)
    if "$" in text.replace("$$", ""):
        sys.exit("renderer.sql still has a $ parameter after substitution")
    header = (f"-- sql/renderer.sql for map {map_id}, player {player_id}, skill "
              f"{skill}, camera read from player_state.\n"
              "-- Returns one row, one column: frame_rgb, 192,000 bytes of RGB.\n")
    return header + text.rstrip().rstrip(";") + ";\n"


def ascii_sql(query, width):
    """Wrap the render query in a SELECT that draws its bytes as text."""
    step_x = W / width
    step_y = step_x * 2  # a character is about twice as tall as it is wide
    lines = int(H // step_y)
    return f"""SELECT r AS line,
       string_agg(substr('{RAMP}', 1 + (b * {len(RAMP) - 1} / 255), 1), '' ORDER BY c) AS picture
FROM (
{query.rstrip().rstrip(';')}
) f
CROSS JOIN generate_series(0, {lines - 1}) AS r
CROSS JOIN generate_series(0, {width - 1}) AS c
CROSS JOIN LATERAL (
  SELECT ((floor(r * {step_y})::int * {W}) + floor(c * {step_x})::int) * 3 AS o) p
CROSS JOIN LATERAL (
  SELECT GREATEST(get_byte(f.frame_rgb, p.o), get_byte(f.frame_rgb, p.o + 1),
                  get_byte(f.frame_rgb, p.o + 2)) AS b) px
GROUP BY r
ORDER BY r;
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", choices=("cedar", "pg"), default="cedar",
                    help="cedar uses $DB_DSN, pg uses $PG_DSN (default: cedar)")
    ap.add_argument("--map", type=int, default=1)
    ap.add_argument("--player", type=int, default=0)
    ap.add_argument("--skill", type=int, default=2)
    ap.add_argument("--png", help="save the frame as a PNG (needs pygame)")
    ap.add_argument("--ascii", action="store_true", help="draw the frame as text, in SQL")
    ap.add_argument("--width", type=int, default=160, help="--ascii columns (default 160)")
    ap.add_argument("--sql", help="write the standalone query (and, with --ascii, "
                                  "the text-art query next to it) and exit")
    args = ap.parse_args()

    query = standalone(args.map, args.player, args.skill, args.engine)
    if args.sql:
        out = Path(args.sql)
        out.write_text(query, encoding="utf-8")
        print(f"wrote {out} ({len(query.splitlines()):,} lines)")
        if args.ascii:
            art = out.with_name(out.stem + "_ascii" + out.suffix)
            art.write_text(ascii_sql(query, args.width), encoding="utf-8")
            print(f"wrote {art}")
        return

    dsn = os.environ.get("DB_DSN" if args.engine == "cedar" else "PG_DSN")
    if not dsn:
        sys.exit("set DB_DSN / PG_DSN first: set -a; source .env; set +a")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    if args.engine == "pg":
        cur.execute("SET jit = off")

    t0 = time.perf_counter()
    cur.execute(query)
    rows = cur.fetchall()
    ms = (time.perf_counter() - t0) * 1000
    frame = bytes(rows[0][0]) if rows and rows[0][0] is not None else b""
    names = [d[0] for d in cur.description]
    print(f"{'CedarDB' if args.engine == 'cedar' else 'PostgreSQL'}: "
          f"{len(rows)} row x {len(names)} column ({', '.join(names)}) in {ms:.0f} ms")
    print(f"  {len(frame):,} bytes = {W} x {H} pixels x 3 (R, G, B)")
    if len(frame) != W * H * 3:
        sys.exit("  unexpected size: is the player standing inside the map?")
    first = " ".join(frame[i:i + 3].hex() for i in range(0, 12, 3))
    print(f"  first 4 pixels (top-left, RGB hex): {first}")

    if args.png:
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame
        pygame.image.save(pygame.image.frombuffer(frame, (W, H), "RGB"), args.png)
        print(f"  saved {args.png}")

    if args.ascii:
        t0 = time.perf_counter()
        cur.execute(ascii_sql(query, args.width))
        art = cur.fetchall()
        print(f"\n  drawn in SQL: {len(art)} lines in "
              f"{(time.perf_counter() - t0) * 1000:.0f} ms\n")
        for _, line in art:
            print(line)


if __name__ == "__main__":
    main()
