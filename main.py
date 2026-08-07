import base64
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

import requests
from flask import Flask, Response, jsonify

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("blockwatch")

CAMERAS_URL = "https://webcams.nyctmc.org/api/cameras"
FRAME_URL = "https://webcams.nyctmc.org/api/cameras/{id}/image"
ROBOFLOW_URL = "https://detect.roboflow.com/{model}"
MODEL_ID = os.environ.get("ROBOFLOW_MODEL", "coco/24")
CLASSES = ["car", "truck", "bus", "person", "bicycle"]
POLL_INTERVAL = 3
WINDOW_SECONDS = 20 * 60

app = Flask(__name__)

_lock = threading.Lock()
_camera = None            # selected camera dict from the TMC API
_latest_frame = None      # most recent JPEG bytes
_latest_frame_ts = None
_history = deque(maxlen=WINDOW_SECONDS // POLL_INTERVAL + 20)


def select_camera():
    resp = requests.get(CAMERAS_URL, timeout=15)
    resp.raise_for_status()
    online = [c for c in resp.json() if c.get("isOnline") == "true"]
    if not online:
        raise RuntimeError("no online cameras returned by the TMC API")
    wanted = os.environ.get("CAMERA_ID")
    if wanted:
        for cam in online:
            if cam.get("id") == wanted:
                return cam
        log.warning("CAMERA_ID %s not found among online cameras; falling back", wanted)
    for cam in online:
        if cam.get("area") == "Manhattan":
            return cam
    return online[0]


def fetch_frame(camera_id):
    resp = requests.get(FRAME_URL.format(id=camera_id), timeout=15)
    resp.raise_for_status()
    return resp.content


def infer_counts(frame):
    api_key = os.environ.get("ROBOFLOW_API_KEY")
    if not api_key:
        raise RuntimeError("ROBOFLOW_API_KEY is not set")
    resp = requests.post(
        ROBOFLOW_URL.format(model=MODEL_ID),
        params={"api_key": api_key},
        data=base64.b64encode(frame),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    counts = {cls: 0 for cls in CLASSES}
    for pred in resp.json().get("predictions", []):
        cls = pred.get("class")
        if cls in counts:
            counts[cls] += 1
    return counts


def prune_history(now):
    cutoff = now - WINDOW_SECONDS
    while _history and _history[0]["epoch"] < cutoff:
        _history.popleft()


def watch_loop():
    global _camera, _latest_frame, _latest_frame_ts
    while True:
        started = time.time()
        try:
            if _camera is None:
                cam = select_camera()
                with _lock:
                    _camera = cam
                log.info("watching camera %s (%s, %s)", cam["id"], cam.get("name"), cam.get("area"))
            frame = fetch_frame(_camera["id"])
            now = time.time()
            with _lock:
                _latest_frame = frame
                _latest_frame_ts = now
            counts = infer_counts(frame)
            entry = {
                "epoch": now,
                "timestamp": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                "counts": counts,
            }
            with _lock:
                _history.append(entry)
                prune_history(now)
        except Exception as exc:
            log.warning("frame skipped: %s", exc)
        time.sleep(max(0.0, POLL_INTERVAL - (time.time() - started)))


def start_watcher():
    thread = threading.Thread(target=watch_loop, name="blockwatch", daemon=True)
    thread.start()


start_watcher()


@app.route("/api/stats")
def api_stats():
    with _lock:
        camera = dict(_camera) if _camera else None
        entries = [
            {"timestamp": e["timestamp"], "epoch": e["epoch"], "counts": e["counts"]}
            for e in _history
        ]
        frame_ts = _latest_frame_ts
    latest = entries[-1] if entries else None
    return jsonify({
        "camera": camera,
        "model": MODEL_ID,
        "classes": CLASSES,
        "window_seconds": WINDOW_SECONDS,
        "poll_interval": POLL_INTERVAL,
        "latest": latest,
        "frame_timestamp": frame_ts,
        "history": entries,
    })


@app.route("/frame")
def frame():
    with _lock:
        data = _latest_frame
    if data is None:
        return Response("no frame yet", status=503, mimetype="text/plain")
    return Response(data, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Block Watch</title>
<style>
  :root {
    color-scheme: light;
    --page: #f9f9f7; --surface: #fcfcfb;
    --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
    --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
    --s-car: #2a78d6; --s-truck: #eb6834; --s-bus: #1baf7a;
    --s-person: #eda100; --s-bicycle: #e87ba4;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      color-scheme: dark;
      --page: #0d0d0d; --surface: #1a1a19;
      --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
      --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
      --s-car: #3987e5; --s-truck: #d95926; --s-bus: #199e70;
      --s-person: #c98500; --s-bicycle: #d55181;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px; background: var(--page); color: var(--ink);
    font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  .wrap { max-width: 960px; margin: 0 auto; display: grid; gap: 16px; }
  header h1 { margin: 0; font-size: 20px; }
  header .sub { color: var(--ink-2); font-size: 13px; margin-top: 2px; }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px;
  }
  .cam-row { display: grid; grid-template-columns: minmax(0, 1fr) 220px; gap: 16px; }
  @media (max-width: 720px) { .cam-row { grid-template-columns: 1fr; } }
  #frame { width: 100%; border-radius: 6px; display: block; background: var(--grid); min-height: 180px; }
  .tiles { display: grid; gap: 8px; align-content: start; }
  .tile { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
  .tile .label { display: flex; align-items: center; gap: 8px; color: var(--ink-2); font-size: 13px; }
  .chip { width: 10px; height: 10px; border-radius: 3px; flex: none; }
  .tile .val { font-size: 20px; font-weight: 600; }
  .meta { color: var(--muted); font-size: 12px; }
  h2 { margin: 0 0 4px; font-size: 14px; color: var(--ink-2); font-weight: 600; }
  .chart-wrap { position: relative; }
  canvas { width: 100%; height: 240px; display: block; }
  .legend { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 8px; font-size: 13px; color: var(--ink-2); }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  #tooltip {
    position: absolute; pointer-events: none; display: none;
    background: var(--surface); border: 1px solid var(--border); border-radius: 6px;
    padding: 8px 10px; font-size: 12px; color: var(--ink); box-shadow: 0 2px 8px rgba(0,0,0,.15);
    white-space: nowrap; z-index: 2;
  }
  #tooltip .t { color: var(--muted); margin-bottom: 4px; }
  #tooltip .row { display: flex; align-items: center; gap: 6px; }
  #tooltip .row b { margin-left: auto; padding-left: 12px; font-variant-numeric: tabular-nums; }
  #status { color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Block Watch</h1>
    <div class="sub" id="cam-name">Connecting…</div>
  </header>

  <div class="card cam-row">
    <div>
      <img id="frame" alt="Live traffic camera frame">
      <div class="meta" id="frame-meta"></div>
    </div>
    <div class="tiles" id="tiles"></div>
  </div>

  <div class="card">
    <h2>Detections per minute (last 20 min)</h2>
    <div class="chart-wrap">
      <canvas id="chart"></canvas>
      <div id="tooltip"></div>
    </div>
    <div class="legend" id="legend"></div>
  </div>

  <div id="status"></div>
</div>

<script>
const CLASSES = ["car", "truck", "bus", "person", "bicycle"];
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const color = cls => css("--s-" + cls);

let history = [];
let buckets = [];

function fmtTime(epoch) {
  return new Date(epoch * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function bucketize(entries) {
  const byMin = new Map();
  for (const e of entries) {
    const min = Math.floor(e.epoch / 60) * 60;
    if (!byMin.has(min)) byMin.set(min, { epoch: min, n: 0, sums: Object.fromEntries(CLASSES.map(c => [c, 0])) });
    const b = byMin.get(min);
    b.n++;
    for (const c of CLASSES) b.sums[c] += e.counts[c] || 0;
  }
  return [...byMin.values()].sort((a, b) => a.epoch - b.epoch)
    .map(b => ({ epoch: b.epoch, avg: Object.fromEntries(CLASSES.map(c => [c, b.sums[c] / b.n])) }));
}

function renderTiles(latest) {
  const el = document.getElementById("tiles");
  el.innerHTML = CLASSES.map(c => {
    const v = latest ? (latest.counts[c] ?? 0) : "–";
    return `<div class="tile"><span class="label"><span class="chip" style="background:${color(c)}"></span>${c}</span><span class="val">${v}</span></div>`;
  }).join("");
}

function renderLegend() {
  document.getElementById("legend").innerHTML = CLASSES.map(c =>
    `<span><span class="chip" style="background:${color(c)}"></span>${c}</span>`).join("");
}

function drawChart() {
  const canvas = document.getElementById("chart");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const pad = { l: 30, r: 10, t: 8, b: 22 };
  const pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;
  if (!buckets.length) {
    ctx.fillStyle = css("--muted");
    ctx.font = "13px system-ui";
    ctx.fillText("Waiting for data…", pad.l, h / 2);
    return;
  }

  const maxVal = Math.max(2, ...buckets.flatMap(b => CLASSES.map(c => b.avg[c])));
  const x0 = buckets[0].epoch, x1 = Math.max(buckets[buckets.length - 1].epoch, x0 + 60);
  const X = e => pad.l + (e - x0) / (x1 - x0) * pw;
  const Y = v => pad.t + ph - v / maxVal * ph;

  ctx.strokeStyle = css("--grid");
  ctx.lineWidth = 1;
  ctx.fillStyle = css("--muted");
  ctx.font = "11px system-ui";
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const v = maxVal / ticks * i, y = Y(v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    ctx.fillText(String(Math.round(v * 10) / 10), 2, y + 4);
  }
  ctx.strokeStyle = css("--axis");
  ctx.beginPath(); ctx.moveTo(pad.l, pad.t + ph); ctx.lineTo(w - pad.r, pad.t + ph); ctx.stroke();

  const step = Math.max(1, Math.ceil(buckets.length / 6));
  ctx.fillStyle = css("--muted");
  buckets.forEach((b, i) => {
    if (i % step === 0) ctx.fillText(fmtTime(b.epoch), X(b.epoch) - 14, h - 6);
  });

  for (const c of CLASSES) {
    ctx.strokeStyle = color(c);
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.beginPath();
    buckets.forEach((b, i) => {
      const x = X(b.epoch), y = Y(b.avg[c]);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
    if (buckets.length === 1) {
      ctx.fillStyle = color(c);
      ctx.beginPath();
      ctx.arc(X(buckets[0].epoch), Y(buckets[0].avg[c]), 3, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  canvas._scale = { X, x0, x1, pad, pw };
}

function onHover(ev) {
  const canvas = document.getElementById("chart");
  const tip = document.getElementById("tooltip");
  const s = canvas._scale;
  if (!s || !buckets.length) { tip.style.display = "none"; return; }
  const rect = canvas.getBoundingClientRect();
  const px = ev.clientX - rect.left;
  let best = null, bestD = Infinity;
  for (const b of buckets) {
    const d = Math.abs(s.X(b.epoch) - px);
    if (d < bestD) { bestD = d; best = b; }
  }
  if (!best || bestD > 40) { tip.style.display = "none"; return; }
  tip.innerHTML = `<div class="t">${fmtTime(best.epoch)} avg/frame</div>` + CLASSES.map(c =>
    `<div class="row"><span class="chip" style="background:${color(c)}"></span>${c}<b>${(Math.round(best.avg[c] * 10) / 10).toFixed(1)}</b></div>`).join("");
  tip.style.display = "block";
  const tx = Math.min(s.X(best.epoch) + 12, rect.width - tip.offsetWidth - 4);
  tip.style.left = Math.max(0, tx) + "px";
  tip.style.top = "12px";
}

async function refresh() {
  try {
    const resp = await fetch("/api/stats");
    const data = await resp.json();
    if (data.camera) {
      document.getElementById("cam-name").textContent =
        `${data.camera.name} — ${data.camera.area} (${data.camera.latitude.toFixed(5)}, ${data.camera.longitude.toFixed(5)}) · model ${data.model}`;
    }
    history = data.history || [];
    buckets = bucketize(history);
    renderTiles(data.latest);
    drawChart();
    if (data.frame_timestamp) {
      document.getElementById("frame").src = "/frame?t=" + data.frame_timestamp;
      document.getElementById("frame-meta").textContent = "Frame at " + fmtTime(data.frame_timestamp);
    }
    document.getElementById("status").textContent =
      history.length ? `${history.length} samples in window · updated ${new Date().toLocaleTimeString()}` : "Waiting for first detection…";
  } catch (err) {
    document.getElementById("status").textContent = "Fetch failed: " + err;
  }
}

renderLegend();
renderTiles(null);
drawChart();
refresh();
setInterval(refresh, 3000);
window.addEventListener("resize", drawChart);
document.getElementById("chart").addEventListener("mousemove", onHover);
document.getElementById("chart").addEventListener("mouseleave", () => {
  document.getElementById("tooltip").style.display = "none";
});
if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { renderLegend(); drawChart(); });
}
</script>
</body>
</html>
"""


@app.route("/")
def home():
    return Response(DASHBOARD_HTML, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
