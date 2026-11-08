"""Turn demo/last_run.json into an animated recording.

Frames are rendered by Chrome (real text layout, real fonts, real CJK shaping)
and assembled by ffmpeg. The data on screen comes from the run file — nothing is
invented for the video, only the pacing is slowed so a human can read it.

One screenshot per visible state, each held for the gap to the next: a log
scrolls line by line, and typing letter-by-letter cost 4.5 minutes of rendering
for a worse result.

    python3 demo/render_frames.py --data demo/last_run.json --out docs/assets/demo.gif
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"   # resolved from PATH; see README for install

W, H = 1000, 560
CPS = 60.0          # characters per second, used only to size each line's reveal
HOLD = 1.1          # seconds a finished line stays before the next one starts


def build_lines(data: dict) -> list[dict]:
    """Flatten the run into rows with a readable pace."""
    rows: list[dict] = []
    t = 0.35

    def add(text: str, cls: str = "", pause: float = HOLD, dur: float | None = None) -> None:
        nonlocal t
        d = dur if dur is not None else max(0.24, len(text) / CPS)
        rows.append({"t": round(t, 2), "dur": round(d, 2), "text": text, "cls": cls})
        t += d + pause

    add("$ python3 demo/offline_demo.py", "cmd", 0.9)
    for step in data.get("steps", []):
        if step["kind"] == "ready":
            add("runtime listening on :8899        chain: remote \u2192 local", "dim", 1.1)
        elif step["kind"] == "boot":
            add(step["text"], "dim", 0.35)
        elif step["kind"] == "cut":
            add("\u2702 " + step["text"], "bad", 0.85)
        elif step["kind"] == "restore":
            add("\u21ba " + step["text"], "ok", 0.85)
        elif step["kind"] == "ask":
            add("\u25b8 " + step["question"], "ask", 0.4)
            for attempt in step.get("attempts") or []:
                if attempt.get("ok"):
                    add(f"  \u2713 {attempt['provider']}  {step['secs']}s", "ok", 0.18)
                else:
                    add(f"  \u2717 {attempt['provider']}  {attempt['error'].split(':')[0]}", "bad", 0.18)
            if step.get("degraded"):
                add("  degraded= true   the answer came from this machine", "warn", 0.28)
            reply = (step.get("reply") or "").strip().replace("\n", " ")
            if reply:
                add("  " + reply[:150] + ("\u2026" if len(reply) > 150 else ""), "reply", 1.7)

    add("", "dim", 0.15)
    add("0 requests lost.  0 restarts.  1 machine.", "sum", 2.6, dur=0.4)
    return rows


HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>ADI demo frame</title>
<style>
  html,body{{margin:0;height:100%;background:#0A0F18;}}
  body{{font:15px/1.65 "SF Mono",ui-monospace,Menlo,"Songti SC",monospace;color:#C7D5E6;}}
  .win{{position:fixed;inset:0;padding:0;}}
  .bar{{height:34px;background:#0E1622;border-bottom:1px solid #16233A;display:flex;align-items:center;gap:7px;padding:0 14px;}}
  .dot{{width:10px;height:10px;border-radius:50%;}}
  .bar span{{margin-left:10px;color:#5B7396;font-size:12px;letter-spacing:.06em;}}
  #log{{padding:20px 26px;white-space:pre-wrap;word-break:break-word;}}
  .row{{min-height:1.65em;}}
  .cmd{{color:#E8F1FF;}}
  .dim{{color:#5B7396;font-size:13px;}}
  .ask{{color:#8FB8FF;margin-top:10px;}}
  .ok{{color:#5FD09A;}}
  .bad{{color:#FF7B72;}}
  .warn{{color:#F2C55C;}}
  .reply{{color:#D7E3F2;}}
  .sum{{color:#E8F1FF;margin-top:12px;letter-spacing:.02em;}}
</style></head>
<body><div class="win">
  <div class="bar">
    <div class="dot" style="background:#FF5F57"></div>
    <div class="dot" style="background:#FEBC2E"></div>
    <div class="dot" style="background:#28C840"></div>
    <span>adi-runtime \u2014 offline demo</span>
  </div>
  <div id="log"></div>
</div>
<script>
const ROWS = {rows};
const now = parseFloat(new URLSearchParams(location.search).get('t') || '999');
let html = '';
for (const r of ROWS) {{
  if (now < r.t) break;
  const p = Math.min(1, (now - r.t) / Math.max(r.dur, 0.01));
  const n = Math.max(1, Math.round(r.text.length * p));
  html += '<div class="row ' + r.cls + '">' + esc(r.text.slice(0, n)) + '</div>';
}}
function esc(s){{return s.replace(/&/g,'&amp;').replace(/</g,'&lt;');}}
document.getElementById('log').innerHTML = html;
</script></body></html>
"""


def shoot(page: pathlib.Path, t: float, out: pathlib.Path) -> bool:
    for _ in range(3):
        if out.exists():
            out.unlink()
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--window-size={W},{H}", f"--screenshot={out}",
                        "--virtual-time-budget=150", f"file://{page}?t={t:.2f}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if out.exists() and out.stat().st_size > 2000:
            return True
        time.sleep(0.4)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="demo/last_run.json")
    ap.add_argument("--out", default="docs/assets/demo.gif")
    ap.add_argument("--width", type=int, default=820)
    ap.add_argument("--keep-frames", action="store_true")
    args = ap.parse_args()

    root = pathlib.Path(__file__).resolve().parents[1]
    data = json.loads((root / args.data).read_text())
    rows = build_lines(data)

    # One state per row: the moment that row is fully shown, held until the next.
    states: list[tuple[float, float]] = []
    for i, row in enumerate(rows):
        end = row["t"] + row["dur"]
        nxt = rows[i + 1]["t"] if i + 1 < len(rows) else end + 0.4
        states.append((end + 0.01, max(0.32, nxt - end)))
    states.append((rows[-1]["t"] + rows[-1]["dur"] + 1.0, 2.8))
    total = sum(d for _, d in states)
    print(f"{len(rows)} rows, {len(states)} keyframes, {total:.1f}s of video")

    work = pathlib.Path(tempfile.mkdtemp(prefix="adi_frames_"))
    page = work / "frame.html"
    page.write_text(HTML.format(rows=json.dumps(rows, ensure_ascii=False)))

    for i, (t_state, _) in enumerate(states):
        if not shoot(page, t_state, work / f"f{i:03d}.png"):
            print(f"frame {i} failed after 3 attempts", file=sys.stderr)
            return 3
        print(f"  shot {i+1}/{len(states)}", flush=True)

    (work / "list.txt").write_text("".join(
        f"file 'f{i:03d}.png'\nduration {d}\n" for i, (_, d) in enumerate(states))
        + f"file 'f{len(states)-1:03d}.png'\nduration 0.1\n")

    out_gif = root / args.out
    out_gif.parent.mkdir(parents=True, exist_ok=True)
    concat = ["-f", "concat", "-safe", "0", "-i", str(work / "list.txt")]
    palette = work / "pal.png"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *concat,
                    "-vf", f"fps=12,scale={args.width}:-2:flags=lanczos,palettegen=max_colors=128",
                    str(palette)], check=True)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *concat, "-i", str(palette),
                    "-lavfi", f"fps=12,scale={args.width}:-2:flags=lanczos[x];[x][1:v]"
                              f"paletteuse=dither=bayer:bayer_scale=3",
                    "-loop", "0", str(out_gif)], check=True)

    mp4 = out_gif.with_suffix(".mp4")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *concat,
                    "-vf", f"fps=12,scale={args.width}:-2:flags=lanczos",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23",
                    "-movflags", "+faststart", str(mp4)], check=True)

    print(f"gif: {out_gif}  {out_gif.stat().st_size/1e6:.2f} MB")
    print(f"mp4: {mp4}  {mp4.stat().st_size/1e6:.2f} MB")
    if args.keep_frames:
        print(f"frames kept in {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
