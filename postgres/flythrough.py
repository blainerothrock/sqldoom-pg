#!/usr/bin/env python3
"""Spin the camera through 360 degrees on CedarDB and on PostgreSQL, and replay
both on their real, measured timelines in a single HTML page.

Run postgres/clone_to_postgres.py first so both databases hold the same world.
The spin starts from the player's pose in the Postgres snapshot and turns in
place, so every pose is valid wherever the player is standing.

    set -a; source .env; set +a          # DB_DSN = CedarDB, PG_DSN = Postgres
    python3 postgres/flythrough.py [--frames 36] [--jit on|off] [--workers N]
                                   [--out postgres/flythrough/flythrough.html]

Each engine renders every pose once, warm (one warm-up execution of the
prepared statement first), CedarDB first and then Postgres, never interleaved.
The page embeds all frames, so it works offline: play it in real time and the
CedarDB side finishes its spin while Postgres is still on its first frames;
the slider scrubs both to the same pose to show the frames are identical.
"""
import argparse
import base64
import io
import json
import os
import sys
import time
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
SQLDOOM = ROOT / "sqldoom"
sys.path.insert(0, str(SQLDOOM))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import doom_sql as sql  # noqa: E402
import port_renderer  # noqa: E402
from doom_config import tune_replanning  # noqa: E402

PARAM_TYPES = ("int", "int", "int", "float8", "float8", "float8", "float8")
W, H = 320, 200


def png_data_uri(frame):
    import pygame
    surface = pygame.image.frombuffer(frame, (W, H), "RGB")
    buf = io.BytesIO()
    pygame.image.save(surface, buf, "frame.png")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def render_all(cur, label, text, params, poses, warmup):
    # CedarDB answers the first execution after PREPARE quickly, then spends
    # ~1.5 s on the second one (it looks like a tiered compile), then runs warm.
    # Warm-up runs are timed and reported but kept off the playback timeline.
    sql.prepare_statement(cur, "flythrough", PARAM_TYPES, text)
    warm = []
    for _ in range(warmup):
        t0 = time.perf_counter()
        sql.execute_prepared(cur, "flythrough", params + poses[0])
        cur.fetchone()
        warm.append((time.perf_counter() - t0) * 1000)
    if warm:
        print(f"{label:<9} warm-up runs: " + ", ".join(f"{t:.0f} ms" for t in warm))
    frames, times = [], []
    for i, pose in enumerate(poses):
        t0 = time.perf_counter()
        sql.execute_prepared(cur, "flythrough", params + pose)
        frame = bytes(cur.fetchone()[0])
        times.append((time.perf_counter() - t0) * 1000)
        frames.append(frame)
        print(f"\r{label:<9} frame {i + 1:>3}/{len(poses)}  {times[-1]:9.1f} ms",
              end="", flush=True)
    total = sum(times)
    print(f"\r{label:<9} {len(poses)} frames in {total / 1000:8.2f} s "
          f"(mean {total / len(poses):8.1f} ms/frame)")
    return frames, times, warm


def pixel_diff(a, b):
    return sum(1 for i in range(0, min(len(a), len(b)), 3) if a[i:i + 3] != b[i:i + 3])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cedar", default=os.environ.get("DB_DSN"), help="default: $DB_DSN")
    ap.add_argument("--pg", default=os.environ.get("PG_DSN"), help="default: $PG_DSN")
    ap.add_argument("--map", type=int, default=1)
    ap.add_argument("--player", type=int, default=0)
    ap.add_argument("--skill", type=int, default=2)
    ap.add_argument("--frames", type=int, default=36, help="poses in the spin (default 36 = 10 degrees each)")
    ap.add_argument("--degrees", type=float, default=360.0, help="how far to turn (default 360)")
    ap.add_argument("--jit", choices=("on", "off"), default="off",
                    help="Postgres jit (default off; on costs ~12 s per frame)")
    ap.add_argument("--workers", type=int, help="Postgres max_parallel_workers_per_gather")
    ap.add_argument("--warmup", type=int, default=2,
                    help="untimed-for-playback runs after PREPARE (default 2; CedarDB needs 2 to be warm)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "flythrough" / "flythrough.html"))
    args = ap.parse_args()
    if not args.cedar or not args.pg:
        ap.error("set DB_DSN and PG_DSN (set -a; source .env; set +a) or pass --cedar/--pg")

    original = sql.load_sql("renderer.sql").rstrip().rstrip(";")
    ported = port_renderer.port(original)

    pg = psycopg2.connect(args.pg)
    pg.autocommit = True
    pc = pg.cursor()
    pc.execute("SET plan_cache_mode = force_generic_plan")
    pc.execute(f"SET jit = {args.jit}")
    if args.workers is not None:
        pc.execute(f"SET max_parallel_workers_per_gather = {args.workers:d}")
    pc.execute("SELECT version(), current_setting('jit'), "
               "current_setting('max_parallel_workers_per_gather')")
    pg_version, jit, workers = pc.fetchone()
    pc.execute("SELECT position_x, position_y, view_z, view_angle FROM player_state "
               "WHERE map_id = %s AND player_thing_id = %s", (args.map, args.player))
    start = pc.fetchone()
    if start is None:
        sys.exit("no player_state row in the Postgres copy -- play once, then re-clone")
    x, y, z, angle = map(float, start)
    step = args.degrees / args.frames
    poses = [(x, y, z, (angle + i * step) % 360.0) for i in range(args.frames)]
    params = (args.map, args.player, args.skill)

    cedar = psycopg2.connect(args.cedar)
    cedar.autocommit = True
    cc = cedar.cursor()
    tune_replanning(cc)
    cc.execute("SELECT version()")
    cedar_version = cc.fetchone()[0]

    print(f"{args.frames} poses, {step:g} degrees apart, from x={x:.0f} y={y:.0f} angle={angle:.0f}")
    print(f"Postgres jit={jit} parallel_workers_per_gather={workers}\n")
    f_cedar, t_cedar, w_cedar = render_all(cc, "CedarDB", original, params, poses, args.warmup)
    f_pg, t_pg, w_pg = render_all(pc, "Postgres", ported, params, poses, args.warmup)
    diffs = [pixel_diff(a, b) for a, b in zip(f_cedar, f_pg)]
    print(f"\nPostgres / CedarDB total time: {sum(t_pg) / sum(t_cedar):.1f}x")
    print(f"pixels that differ: {sum(diffs)} across {args.frames} frames")

    data = {
        "angles": [round(p[3], 1) for p in poses],
        "diffs": diffs,
        "engines": [
            {"name": "CedarDB", "version": cedar_version.split(",")[0],
             "setting": "", "times": [round(t, 2) for t in t_cedar],
             "warmup": [round(t) for t in w_cedar],
             "frames": [png_data_uri(f) for f in f_cedar]},
            {"name": "PostgreSQL", "version": pg_version.split(",")[0],
             "setting": f"jit={jit}, parallel workers per gather={workers}",
             "times": [round(t, 2) for t in t_pg],
             "warmup": [round(t) for t in w_pg],
             "frames": [png_data_uri(f) for f in f_pg]},
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(PAGE.replace("/*DATA*/null", json.dumps(data)), encoding="utf-8")
    print(f"wrote {out}")


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SQLDoom: one spin, two engines</title>
<style>
  :root { color-scheme: light dark; --bg:#111; --fg:#eee; --mute:#999; --line:#333; --acc:#e8a33d; }
  @media (prefers-color-scheme: light) { :root { --bg:#fafafa; --fg:#111; --mute:#666; --line:#ccc; --acc:#b86e00; } }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.4 system-ui, sans-serif; }
  main { max-width:1360px; margin:0 auto; padding:24px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:var(--mute); margin:0 0 20px; }
  .pair { display:grid; grid-template-columns:1fr 1fr; gap:24px; }
  .eng h2 { font-size:17px; margin:0; }
  .eng .ver { color:var(--mute); font-size:13px; min-height:1.4em; }
  img { width:100%; aspect-ratio:320/200; image-rendering:pixelated; display:block; background:#000; margin:8px 0; }
  .stats { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; font-variant-numeric:tabular-nums; }
  .stats div { border-top:1px solid var(--line); padding-top:4px; }
  .stats b { display:block; font-size:22px; font-weight:600; }
  .stats span { color:var(--mute); font-size:12px; }
  .bar { height:6px; background:var(--line); margin-top:10px; }
  .bar i { display:block; height:100%; width:0; background:var(--acc); }
  .controls { display:flex; flex-wrap:wrap; gap:12px; align-items:center; margin:24px 0 8px; }
  button, select { font:inherit; padding:6px 14px; border:1px solid var(--line); background:transparent; color:var(--fg); border-radius:4px; cursor:pointer; }
  input[type=range] { flex:1; min-width:200px; }
  .clock { font-variant-numeric:tabular-nums; min-width:9em; }
  .note { color:var(--mute); font-size:13px; }
</style>
</head>
<body>
<main>
  <h1>One SQL query, two database engines</h1>
  <p class="sub" id="sub"></p>
  <div class="controls">
    <button id="play">Play in real time</button>
    <label>Speed <select id="speed">
      <option value="0.1">0.1x</option><option value="1" selected>1x</option>
      <option value="10">10x</option><option value="60">60x</option>
    </select></label>
    <span class="clock" id="clock">t = 0.00 s</span>
  </div>
  <div class="pair" id="pair"></div>
  <div class="controls">
    <span>Same pose on both</span>
    <input type="range" id="scrub" min="0" value="0">
    <span class="clock" id="pose"></span>
  </div>
  <p class="note">Real-time playback shows each engine's frames on the timeline it actually rendered them on:
    at any moment, each side shows the newest frame it had finished. The slider ignores timing and puts both
    sides on the same pose.</p>
</main>
<script>
const D = /*DATA*/null;
const n = D.angles.length;
const fmt = ms => ms < 1000 ? ms.toFixed(1) + ' ms' : (ms / 1000).toFixed(2) + ' s';
const eng = D.engines.map(e => {
  let c = 0; const cum = e.times.map(t => (c += t));
  return { ...e, cum, total: c };
});
const ratio = eng[1].total / eng[0].total;
const totalDiff = D.diffs.reduce((a, b) => a + b, 0);
document.getElementById('sub').textContent =
  `${n} frames turning ${(D.angles.length > 1 ? (D.angles[1] - D.angles[0] + 360) % 360 : 0).toFixed(0)}° each. ` +
  `PostgreSQL took ${ratio.toFixed(0)}x as long. Pixels that differ: ${totalDiff.toLocaleString()} of ${(n * 64000).toLocaleString()}.`;

const pair = document.getElementById('pair');
const ui = eng.map(e => {
  const el = document.createElement('section'); el.className = 'eng';
  el.innerHTML = `<h2>${e.name}</h2><div class="ver">${e.version}${e.setting ? ' · ' + e.setting : ''}</div>
    <img alt="${e.name} frame">
    <div class="stats">
      <div><b class="f">0</b><span>frames done of ${n}</span></div>
      <div><b class="ms">–</b><span>this frame took</span></div>
      <div><b class="tot">${fmt(e.total)}</b><span>whole spin</span></div>
    </div><div class="bar"><i></i></div>
    <div class="note">${e.warmup.length ? 'Warm-up runs after PREPARE, not on the timeline: ' + e.warmup.map(fmt).join(', ') : ''}</div>`;
  pair.appendChild(el);
  return { img: el.querySelector('img'), f: el.querySelector('.f'), ms: el.querySelector('.ms'), bar: el.querySelector('.bar i') };
});

function show(k, i, done) {
  const e = eng[k], u = ui[k];
  u.img.src = e.frames[Math.max(0, i)];
  u.img.style.opacity = i < 0 ? 0.25 : 1;
  u.f.textContent = done;
  u.ms.textContent = i < 0 ? 'rendering…' : fmt(e.times[i]);
  u.bar.style.width = (100 * done / n) + '%';
}

const clock = document.getElementById('clock'), playBtn = document.getElementById('play');
const speedSel = document.getElementById('speed'), scrub = document.getElementById('scrub'), pose = document.getElementById('pose');
scrub.max = n - 1;
let raf = null, t0 = 0, tOffset = 0, speed = Number(speedSel.value);
const tEnd = Math.max(eng[0].total, eng[1].total);

function frameAt(k, t) {  // newest frame finished by time t
  const cum = eng[k].cum; let lo = 0, hi = cum.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (cum[m] <= t) lo = m + 1; else hi = m; }
  return lo;  // number of frames done
}
function tick(now) {
  const t = Math.min(tEnd, tOffset + (now - t0) * speed);
  clock.textContent = 't = ' + (t / 1000).toFixed(2) + ' s';
  eng.forEach((_, k) => { const done = frameAt(k, t); show(k, done - 1, done); });
  if (t < tEnd) raf = requestAnimationFrame(tick);
  else { raf = null; playBtn.textContent = 'Replay'; }
}
function play() {
  if (raf) { cancelAnimationFrame(raf); raf = null; }
  tOffset = 0; t0 = performance.now(); playBtn.textContent = 'Restart';
  raf = requestAnimationFrame(tick);
}
speedSel.onchange = () => {  // keep the current position, change the rate from here on
  const now = performance.now();
  if (raf) { tOffset += (now - t0) * speed; t0 = now; }
  speed = Number(speedSel.value);
};
playBtn.onclick = play;
scrub.oninput = () => {
  if (raf) { cancelAnimationFrame(raf); raf = null; playBtn.textContent = 'Play in real time'; }
  const i = Number(scrub.value);
  eng.forEach((_, k) => show(k, i, i + 1));
  clock.textContent = '';
  pose.textContent = `angle ${D.angles[i]}° · ${D.diffs[i]} px differ`;
};
scrub.oninput();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
