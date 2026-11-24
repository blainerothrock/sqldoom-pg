#!/usr/bin/env python3
"""Rewrite sqldoom/sql/renderer.sql into a PostgreSQL-compatible copy.

    python3 postgres/port_renderer.py            # writes postgres/renderer_pg.sql

Each patch below is a CedarDB-ism stock Postgres rejects. The patches are
textual and assert that their target text exists, so if renderer.sql changes
upstream this fails loudly instead of producing a half-ported query.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "sqldoom" / "sql" / "renderer.sql"
OUT = Path(__file__).resolve().parent / "renderer_pg.sql"

PATCHES = [
    # 1. Lateral column aliases: CedarDB lets a select-list item use an alias
    #    defined earlier in the same SELECT. Postgres needs a derived table.
    (
        """    SELECT
      320::int  AS screen_w,
      168::int  AS screen_h,  -- 200 minus the real 32px status bar
      radians(90.0)::float8 AS fov_rad,
      (screen_w/2.0) AS cx,""",
        """    SELECT b.screen_w, b.screen_h, b.fov_rad,
      (screen_w/2.0) AS cx,""",
    ),
    (
        """      64::int AS flat_size
), cam AS (""",
        """      64::int AS flat_size
    FROM (SELECT 320::int AS screen_w,
                 168::int AS screen_h,  -- 200 minus the real 32px status bar
                 radians(90.0)::float8 AS fov_rad) b
), cam AS (""",
    ),
]


def port(text):
    for old, new in PATCHES:
        if text.count(old) != 1:
            raise SystemExit(f"patch target not found exactly once:\n{old[:120]}")
        text = text.replace(old, new)
    return text


if __name__ == "__main__":
    OUT.write_text(port(SRC.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
