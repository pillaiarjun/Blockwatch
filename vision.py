"""Vision half of Block Watch: camera polling + Roboflow inference.

Every POLL_INTERVAL seconds, fetches the current frame from the selected NYC
TMC camera and runs it through Roboflow hosted inference, publishing the frame,
camera metadata, and per-class counts into `state`. Runs in a daemon thread
started by start().
"""

import base64
import logging
import os
import threading
import time

import requests

import state

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("vision")

CAMERAS_URL = "https://webcams.nyctmc.org/api/cameras"
FRAME_URL = "https://webcams.nyctmc.org/api/cameras/{id}/image"
ROBOFLOW_URL = "https://detect.roboflow.com/{model}"
MODEL_ID = os.environ.get("ROBOFLOW_MODEL", "coco/24")
CLASSES = ["car", "truck", "bus", "person", "bicycle"]
POLL_INTERVAL = 3

_started = False
_camera = None


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
    # Roboflow hosted inference rejects raw JPEG bytes — body must be base64.
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


def watch_loop():
    global _camera
    while True:
        started = time.time()
        try:
            if _camera is None:
                _camera = select_camera()
                log.info(
                    "watching camera %s (%s, %s)",
                    _camera["id"], _camera.get("name"), _camera.get("area"),
                )
                state.set_camera_meta({
                    "id": _camera["id"],
                    "name": _camera.get("name"),
                    "latitude": _camera.get("latitude"),
                    "longitude": _camera.get("longitude"),
                    "area": _camera.get("area"),
                })
            frame = fetch_frame(_camera["id"])
            state.set_frame(frame)
            state.set_counts(infer_counts(frame))
        except Exception as exc:
            log.warning("frame skipped: %s", exc)
        time.sleep(max(0.0, POLL_INTERVAL - (time.time() - started)))


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=watch_loop, name="vision", daemon=True).start()
