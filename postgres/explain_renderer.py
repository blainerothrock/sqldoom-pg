#!/usr/bin/env python3
"""Save the renderer's real query plan from CedarDB and from PostgreSQL.

Run postgres/clone_to_postgres.py first so both databases hold the same world.
Both plans are for the same frame: the player's pose in the Postgres snapshot.

    set -a; source .env; set +a          # DB_DSN = CedarDB, PG_DSN = Postgres
    python3 postgres/explain_renderer.py [--jit off|on] [--out postgres/plans]

Writes, per engine (cedar_*, postgres_*):
  *_plan.txt   EXPLAIN ANALYZE as text -- the tree to scroll through
  *_plan.json  the same plan as JSON (Postgres JSON pastes into plan visualizers)
  *_plan.svg   a poster drawn with scripts/plan_poster.py's layout: depth left
               to right, node size = rows the operator really produced (log scale),
               colour = kind of operator. Postgres node types are mapped onto
               plan_poster's categories, so the same colour means the same kind of
               work on both posters.

Postgres runs the plan as the benchmark does: prepared, generic plan, jit off
by default (jit on adds ~10 s of compiling and a JIT section to the text).
"""
import argparse
import json
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
SQLDOOM = ROOT / "sqldoom"
sys.path.insert(0, str(SQLDOOM))
sys.path.insert(0, str(SQLDOOM / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import os  # noqa: E402

import doom_sql as sql  # noqa: E402
import plan_poster  # noqa: E402
import port_renderer  # noqa: E402
from doom_config import tune_replanning  # noqa: E402

PARAM_TYPES = ("int", "int", "int", "float8", "float8", "float8", "float8")

# PostgreSQL node type -> plan_poster's operator category (its colour key).
PG_CATEGORY = {
    "Nested Loop": "join", "Hash Join": "join", "Merge Join": "join",
    "Seq Scan": "tablescan", "Index Scan": "tablescan", "Index Only Scan": "tablescan",
    "Bitmap Heap Scan": "tablescan", "Bitmap Index Scan": "tablescan",
    "CTE Scan": "pipelinebreakerscan", "WorkTable Scan": "pipelinebreakerscan",
    "Subquery Scan": "pipelinebreakerscan",
    "Aggregate": "groupby", "Group": "groupby",
    "WindowAgg": "window",
    "Sort": "sort", "Incremental Sort": "sort",
    "Function Scan": "generateseries",
    "Append": "setoperation", "Merge Append": "setoperation",
    "Recursive Union": "setoperation", "SetOp": "setoperation",
    "Hash": "temp", "Materialize": "temp", "Memoize": "temp",
    "Result": "map", "ProjectSet": "map",
    "Values Scan": "inlinetable",
    "Limit": "select", "Unique": "select",
}


def pg_tree(node, depth=0):
    """A PostgreSQL JSON plan node in plan_poster's tree shape."""
    loops = node.get("Actual Loops", 1) or 1
    return {
        "op": PG_CATEGORY.get(node["Node Type"], node["Node Type"].lower()),
        "physical": node["Node Type"],
        "rows": int(round(node.get("Actual Rows", 0) * loops)),
        "depth": depth,
        "children": [pg_tree(child, depth + 1) for child in node.get("Plans", ())],
    }


def poster(tree, engine, extra, path, row_step=13.0, col_step=46.0):
    """plan_poster's own layout and SVG, with an engine-specific header."""
    rows = plan_poster.layout(tree, row_step, col_step)
    nodes = plan_poster.flatten(tree)
    depth = max(n["depth"] for n in nodes)
    total_rows = sum(n["rows"] for n in nodes)
    stats = [
        engine,
        f"{len(nodes)} operators, {depth + 1} deep, "
        f"{len(set(n['op'] for n in nodes))} kinds",
        f"{total_rows:,} rows through the plan for this one frame",
        *extra,
        "node size and edge weight are measured rows (log scale)",
    ]
    ts = plan_poster.type_scale(round(260 + depth * col_step + 260))
    header = (34 + len(stats) * 20 + 70) * ts
    margin_left, margin_top = round(360 * ts), round(max(150 * ts, header))
    width = round(margin_left + depth * col_step + 260)
    height = round(margin_top + rows * row_step + 60)
    for node in nodes:
        node["x"] += margin_left
        node["y"] += margin_top
    path.write_text(plan_poster.svg(nodes, width, height, stats), encoding="utf-8")
    return len(nodes), total_rows, width, height


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cedar", default=os.environ.get("DB_DSN"), help="default: $DB_DSN")
    ap.add_argument("--pg", default=os.environ.get("PG_DSN"), help="default: $PG_DSN")
    ap.add_argument("--map", type=int, default=1)
    ap.add_argument("--player", type=int, default=0)
    ap.add_argument("--skill", type=int, default=2)
    ap.add_argument("--jit", choices=("on", "off"), default="off")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "plans"))
    args = ap.parse_args()
    if not args.cedar or not args.pg:
        ap.error("set DB_DSN and PG_DSN (set -a; source .env; set +a) or pass --cedar/--pg")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    original = sql.load_sql("renderer.sql").rstrip().rstrip(";")

    # ---- PostgreSQL: prepared + generic plan, exactly as bench_frame runs it
    pg = psycopg2.connect(args.pg)
    pg.autocommit = True
    pc = pg.cursor()
    pc.execute("SET plan_cache_mode = force_generic_plan")
    pc.execute(f"SET jit = {args.jit}")
    pc.execute("SELECT position_x, position_y, view_z, view_angle FROM player_state "
               "WHERE map_id = %s AND player_thing_id = %s", (args.map, args.player))
    pose = pc.fetchone()
    if pose is None:
        sys.exit("no player_state row in the Postgres copy -- play once, then re-clone")
    params = (args.map, args.player, args.skill, *map(float, pose))
    sql.prepare_statement(pc, "explain_frame", PARAM_TYPES, port_renderer.port(original))
    sql.execute_prepared(pc, "explain_frame", params)  # warm-up
    pc.fetchone()
    placeholders = ",".join(["%s"] * len(params))
    pc.execute(f"EXPLAIN (ANALYZE, BUFFERS) EXECUTE explain_frame({placeholders})", params)
    pg_text = "\n".join(r[0] for r in pc.fetchall())
    pc.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) EXECUTE explain_frame({placeholders})", params)
    pg_json = pc.fetchone()[0]
    (out / "postgres_plan.txt").write_text(pg_text + "\n", encoding="utf-8")
    (out / "postgres_plan.json").write_text(json.dumps(pg_json, indent=1), encoding="utf-8")
    pg_doc = pg_json[0]
    pg_extra = [f"PostgreSQL, jit={args.jit}: execution {pg_doc['Execution Time']:.0f} ms"]
    n, total, w, h = poster(pg_tree(pg_doc["Plan"]), "PostgreSQL (prepared, generic plan)",
                            pg_extra, out / "postgres_plan.svg")
    print(f"PostgreSQL: {n} operators, {total:,} rows, "
          f"{pg_doc['Execution Time']:.0f} ms -> {out}/postgres_plan.{{txt,json,svg}}")

    # ---- CedarDB: its EXPLAIN takes the statement text, so inline the parameters
    cedar = psycopg2.connect(args.cedar)
    cedar.autocommit = True
    cc = cedar.cursor()
    tune_replanning(cc)
    body = original
    for i, value in enumerate(params, start=1):
        body = body.replace(f"${i}", repr(value) if i > 3 else str(value))
    cc.execute(body)  # warm-up
    cc.fetchone()
    cc.execute("EXPLAIN ANALYZE " + body)
    cedar_text = cc.fetchone()[0]
    cc.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + body)
    cedar_json = json.loads(cc.fetchone()[0])
    (out / "cedar_plan.txt").write_text(cedar_text + "\n", encoding="utf-8")
    (out / "cedar_plan.json").write_text(json.dumps(cedar_json, indent=1), encoding="utf-8")
    n, total, w, h = poster(plan_poster.build(cedar_json["plan"]), "CedarDB", [],
                            out / "cedar_plan.svg")
    print(f"CedarDB:    {n} operators, {total:,} rows -> {out}/cedar_plan.{{txt,json,svg}}")
    (out / "index.html").write_text(INDEX, encoding="utf-8")
    print(f"side by side: {out}/index.html")


INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Renderer query plan: CedarDB vs PostgreSQL</title>
<style>
  body { margin: 0; background: #0a0b09; color: #d7d3c2; font: 15px/1.4 system-ui, sans-serif; }
  main { padding: 20px 24px; }
  h1 { font-size: 20px; margin: 0 0 4px; color: #75d38b; }
  p { color: #7d7a6c; margin: 0 0 16px; }
  .pair { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; align-items: start; }
  h2 { font-size: 16px; margin: 0 0 6px; }
  img { width: 100%; display: block; border: 1px solid #2a2b27; }
  a { color: #75d38b; }
</style></head>
<body><main>
  <h1>One frame of sql/renderer.sql: the plan each engine ran</h1>
  <p>Same query, same pose, same data. Depth runs left to right; node size is the rows each
     operator really produced (log scale); colour is the kind of operator, mapped to the same
     categories on both. Click a poster for full size.</p>
  <div class="pair">
    <section><h2>CedarDB</h2>
      <a href="cedar_plan.svg"><img src="cedar_plan.svg" alt="CedarDB plan"></a>
      <p><a href="cedar_plan.txt">EXPLAIN ANALYZE text</a> · <a href="cedar_plan.json">JSON</a></p></section>
    <section><h2>PostgreSQL</h2>
      <a href="postgres_plan.svg"><img src="postgres_plan.svg" alt="PostgreSQL plan"></a>
      <p><a href="postgres_plan.txt">EXPLAIN ANALYZE text</a> · <a href="postgres_plan.json">JSON</a></p></section>
  </div>
</main></body></html>
"""


if __name__ == "__main__":
    main()
