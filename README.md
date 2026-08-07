# Block Watch

**An AI agent that watches one NYC block and tells you what's happening on it, in plain English.**

Built at the AI Tinkerers NYC Vision Hack (August 2026).

## The problem

New York City publishes live feeds from hundreds of DOT traffic cameras, but nobody
actually *watches* them — the feeds are raw pixels with no interpretation. Whether
you're a resident wondering why your street is gridlocked, a small business tracking
foot traffic, or a community board arguing for a stop sign, the information is
technically public and practically useless.

Block Watch turns one of those cameras into a narrated, queryable view of a single
block: live detections, minute-by-minute trends, and a running plain-English
commentary of what's happening right now.

## Architecture

```
NYC DOT camera feed ──> Roboflow object detection ──> shared in-memory state
                                                            │
                              Gemini narration loop <───────┤
                                                            │
                              Flask dashboard  <────────────┘
                              (deployed on Google Cloud Run)
```

- **`vision.py`** — polls the NYC DOT camera for the configured `CAMERA_ID`, runs
  each frame through Roboflow object detection, and pushes the JPEG + object counts
  into shared state.
- **`state.py`** — a small thread-safe in-memory store (frame, counts, camera
  metadata, narration, and a rolling 200-entry history of timestamped counts). This
  is the only contract between the vision and narration halves of the app.
- **`narrate.py`** — a daemon thread that, every 60 seconds, summarizes the recent
  detection history and asks Gemini to describe what's happening on the block in
  2–3 sentences.
- **`app.py`** — Flask app serving the dashboard, a JSON state API (`/api/state`),
  the latest frame (`/frame.jpg`), and a health check (`/healthz`).
- **`templates/index.html`** — a self-contained dark dashboard (vanilla JS, no build
  step): live frame, stat cards for current counts, the Gemini narration, and an SVG
  per-minute detection trend.

Everything is in-memory by design — one camera, one Cloud Run instance, no database.
If the instance restarts, state rebuilds itself within a couple of minutes of camera
polling. Honest limitation: this means history is lost on restart and the app is not
horizontally scalable as-is.

## Running locally

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...     # required for narration
export ROBOFLOW_API_KEY=...   # required by vision.py
export CAMERA_ID=...          # NYC DOT camera to watch
python app.py                 # serves on http://localhost:8080
```

The dashboard degrades gracefully: it shows "waiting for first frame…" until the
vision loop produces data, and narration starts about a minute after the first
detections.

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `ROBOFLOW_API_KEY` | Roboflow inference API key (used by `vision.py`) | — |
| `GEMINI_API_KEY` | Google Gemini API key for narration | — (narration skips if unset) |
| `GEMINI_MODEL` | Gemini model name | `gemini-2.0-flash` |
| `CAMERA_ID` | NYC DOT camera to watch (used by `vision.py`) | — |
| `PORT` | HTTP port (set automatically by Cloud Run) | `8080` |

## Deploying to Cloud Run

```bash
gcloud run deploy block-watch \
  --source . \
  --region us-east1 \
  --allow-unauthenticated \
  --set-env-vars "ROBOFLOW_API_KEY=$ROBOFLOW_API_KEY,GEMINI_API_KEY=$GEMINI_API_KEY,CAMERA_ID=$CAMERA_ID"
```

Because state is in-memory, keep the service at a single instance
(`--max-instances 1`) and consider `--min-instances 1` so the camera history
survives between requests.
