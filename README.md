# Block Watch

Point it at one NYC traffic camera and it tells you, in plain English, what is
happening on that block right now.

Built for AI Tinkerers NYC "Vision Hack v.2 — Live Feeds, Open Data," presented
with Google Cloud and sponsored by Roboflow.

## What it does

New York City operates hundreds of public DOT traffic cameras. Their feeds are
free, unauthenticated, and refresh every couple of seconds — and almost nobody
looks at them. The raw output is a wall of low-resolution stills with no
interpretation attached: useful to a traffic engineer staring at a monitor,
useless to a resident who just wants to know why their street is jammed, or a
community board trying to argue that an intersection needs a signal change.

Block Watch adopts a single one of those cameras and makes it legible. It counts
the vehicles and pedestrians in each frame with an object detection model, keeps
a rolling minute-by-minute history of those counts, and — this is the part we
care about — hands that history to Gemini once a minute and asks it to say what
is going on. The result is a live dashboard: the current frame, the current
counts, a per-minute trend, and a running two-or-three-sentence narration like a
neighbor leaning out the window telling you what they see.

Everything is deliberately small. One camera, one Cloud Run instance, no
database, no build step. The point is not scale; the point is that a public
data feed nobody watches can become something a person would actually read.

## Status

The detection half of the pipeline, `vision.py` (camera polling plus Roboflow
inference), is still in progress and is not in this repository yet. The shared
state contract it will write into (`state.py`), the narration loop, the Flask
app, and the dashboard are complete and running; `app.py` already attempts to
import `vision` and start it, and boots cleanly without it. Until `vision.py`
lands, the dashboard shows its "waiting for first frame" state and the
narration loop idles, because narration only fires once at least two detection
samples exist in history. The description of `vision.py` below is its intended
design, not a claim about shipped code.

## How it works

```
NYC DOT camera API  (polled every ~3s)
        |
        v
   vision.py  ── frame ──>  Roboflow hosted inference (HTTP POST)
        |                          |
        |  latest JPEG             |  object counts
        v                          v
   state.py  — thread-safe in-memory store —
   latest JPEG · current counts · 400-entry rolling history · narration
        |                          |
        |                          v
        |                  narrate.py  (daemon thread, every 60s)
        |                          |
        |                          v
        |                     Gemini API
        v
     app.py  (Flask)
     /  ·  /api/state  ·  /frame.jpg  ·  /healthz
        |
        v
  Google Cloud Run  (single instance)
```

`state.py` is the seam between the two halves of the system. It is a small
module-level store guarded by one `threading.Lock`, holding the latest JPEG,
the current counts, camera metadata, the latest narration, and a rolling
history capped at 400 timestamped entries. Every `set_counts()` call appends to
that history automatically, so the vision side never has to think about it.

`narrate.py` runs a daemon thread on a 60-second cycle. Each cycle it reads the
history, skips if there are fewer than two samples, and otherwise formats the
last 40 entries into a timestamped text block and sends it to the Gemini API by
plain HTTPS POST with `requests` — no SDK. A bad model name (HTTP 400/404) is
logged and the previous narration is kept; the whole loop body is wrapped in
try/except so a failed call can never kill the thread.

`app.py` serves the dashboard at `/`, the full state plus history as JSON at
`/api/state`, the latest frame at `/frame.jpg` (with `Cache-Control: no-store`,
returning 204 before the first frame arrives), and a health check at
`/healthz`. The dashboard itself (`templates/index.html`) is one self-contained
page — vanilla JavaScript, inline SVG for the per-minute trend, no CDN
dependencies — that polls `/api/state` and refreshes the frame every 3 seconds.

## Why the narration layer matters

Object counts alone are a vision demo: a model, a bounding box, a number. What
makes Block Watch an agent is the loop that sits above the counts. Every minute
it reads the rolling history — not one frame, but the last several minutes of
change — and produces a judgment in plain English: traffic is building, the
sidewalk is unusually busy for this hour, a truck has been sitting in the
intersection through three cycles. That temporal reading is something no single
frame contains, and plain English is the only interface most people will
actually consume. The counts are the sensor; the narration is the point.

## Data sources

NYC DOT traffic cameras, via the public NYCTMC API: camera metadata from
`https://webcams.nyctmc.org/api/cameras` and stills from
`https://webcams.nyctmc.org/api/cameras/{id}/image`. The feed is free and
public, requires no API key, and refreshes roughly every 2 seconds.

Object detection uses Roboflow hosted inference, called with a plain HTTP POST
from `vision.py` — no SDK dependency.

## Running it locally

```
git clone https://github.com/pillaiarjun/Blockwatch
cd Blockwatch
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export GEMINI_API_KEY=your-key-here
export ROBOFLOW_API_KEY=your-key-here
export CAMERA_ID=your-camera-id

python app.py
```

The app serves on `http://localhost:8080`. It degrades gracefully when data is
missing: without `vision.py` running you get the dashboard in its waiting
state, and without `GEMINI_API_KEY` the narration loop logs a warning each
cycle and skips instead of crashing. To run it the way Cloud Run does, use the
Procfile command: `gunicorn --bind 0.0.0.0:8080 --workers 1 --threads 8
--timeout 0 app:app`.

## Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `GEMINI_API_KEY` | Gemini API key for the narration loop | none; narration skips if unset |
| `GEMINI_MODEL` | Gemini model name | `gemini-2.0-flash` |
| `ROBOFLOW_API_KEY` | Roboflow hosted inference key (used by `vision.py`) | none |
| `CAMERA_ID` | NYC DOT camera to watch (used by `vision.py`) | none |
| `PORT` | HTTP port, injected by Cloud Run | `8080` |

## Deploying to Cloud Run

```
gcloud run deploy block-watch \
  --source . \
  --region us-east1 \
  --allow-unauthenticated \
  --max-instances 1 \
  --set-env-vars "GEMINI_API_KEY=your-key-here,ROBOFLOW_API_KEY=your-key-here,CAMERA_ID=your-camera-id"
```

`--max-instances 1` is required, not a suggestion. All application state lives
in memory inside `state.py`; if Cloud Run scaled the service to two instances,
each would hold its own frame, its own history, and its own narration, and
requests would land on whichever copy the load balancer picked. The Procfile
pins gunicorn to a single worker process with multiple threads for the same
reason. Setting `--min-instances 1` is also worth considering so the rolling
history survives idle periods instead of rebuilding from empty after each cold
start.

## Design tradeoffs and known limitations

Everything below is a deliberate consequence of building the smallest honest
version of this system in one day, and each item is a real limitation, not
false modesty.

State is in memory only. A restart or redeploy wipes the frame, the history,
and the narration; the system rebuilds its picture of the block within a couple
of minutes of polling, but nothing persists, and the single-instance
requirement above follows directly from this choice.

The camera feed is stills, not video. Polling every few seconds yields a low
frame rate, so short events — a cyclist crossing between polls — can be missed
entirely. This is a property of the public feed as much as of our design.

Narration runs on a fixed 60-second cadence. It does not trigger on events, so
a sudden change can take up to a minute to be mentioned, and a quiet block
still costs one Gemini call per minute.

Detection quality is bounded by the chosen Roboflow model. Counts from small,
occluded, or nighttime objects will be noisy, and the narration inherits
whatever the detector gets wrong.

City cameras go dark. Feeds freeze, return stale frames, or drop offline
without notice, and Block Watch has no second camera to fail over to — by
design, since watching one block well was the goal.

## Responsible data use

The camera feeds are public municipal infrastructure, published unauthenticated
by NYC DOT. Block Watch stores no frames on disk: the latest JPEG lives in
memory, is overwritten on every poll, and vanishes when the process stops. The
system counts object classes — cars, trucks, people — and neither identifies
individuals nor tracks anyone across frames. That said, public feeds can
capture faces and license plates, and anything built on them deserves care:
this project works only with the feed's already-low resolution, persists
nothing, and narrates aggregate activity rather than anything about any
particular person or vehicle.

## Team

Arjun Pillai and Soham Banerjee — AI engineers at Hexaware Technologies.
