"""Vision half of Block Watch: camera polling + Roboflow inference.

Every POLL_INTERVAL seconds, fetches the current frame from the selected NYC
TMC camera and runs it through Roboflow hosted inference, publishing the frame,
camera metadata, and per-class counts into `state`. Runs in a daemon thread
started by start().

The current camera is a runtime variable: set_camera(camera_id) switches it
without a restart, and the loop re-reads it every iteration. If no camera is
selected (CAMERA_ID unset and set_camera never called), the loop idles.
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
_camera_lock = threading.Lock()
_camera_id = os.environ.get("CAMERA_ID") or None
_meta_published_for = None  # camera id whose metadata has been pushed to state


def get_camera_id():
    with _camera_lock:
        return _camera_id


def set_camera(camera_id):
    """Switch the polling loop to a new camera without a restart.

    Wipes counts/history/frame/narration via state.reset_for_camera_change()
    so the old block doesn't bleed into the new one; the loop publishes full
    name/coords metadata on its next iteration.
    """
    global _camera_id, _meta_published_for
    with _camera_lock:
        if camera_id == _camera_id:
            return
        _camera_id = camera_id or None
        _meta_published_for = None
    log.info("camera switched to %s", camera_id)
    state.reset_for_camera_change({"id": camera_id})


def _publish_meta(camera_id):
    """Look up the camera in the public list and push its metadata to state."""
    global _meta_published_for
    resp = requests.get(CAMERAS_URL, timeout=15)
    resp.raise_for_status()
    meta = {"id": camera_id, "name": camera_id}
    for cam in resp.json():
        if cam.get("id") == camera_id:
            meta = {
                "id": cam["id"],
                "name": cam.get("name"),
                "latitude": cam.get("latitude"),
                "longitude": cam.get("longitude"),
                "area": cam.get("area"),
            }
            if cam.get("isOnline") != "true":
                log.warning("camera %s is reported offline", camera_id)
            break
    else:
        log.warning("camera %s not in the TMC list; using id as name", camera_id)
    state.set_camera_meta(meta)
    _meta_published_for = camera_id
    log.info("watching camera %s (%s, %s)", camera_id, meta.get("name"), meta.get("area"))


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
    while True:
        started = time.time()
        cam_id = get_camera_id()
        if cam_id is None:
            time.sleep(POLL_INTERVAL)
            continue
        try:
            if _meta_published_for != cam_id:
                _publish_meta(cam_id)
            frame = fetch_frame(cam_id)
            if get_camera_id() != cam_id:  # switched mid-fetch; drop stale frame
                continue
            state.set_frame(frame)
            counts = infer_counts(frame)
            if get_camera_id() != cam_id:  # switched mid-inference; drop stale counts
                continue
            state.set_counts(counts)
        except Exception as exc:
            log.warning("frame skipped: %s", exc)
        time.sleep(max(0.0, POLL_INTERVAL - (time.time() - started)))


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=watch_loop, name="vision", daemon=True).start()
