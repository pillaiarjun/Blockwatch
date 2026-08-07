"""Block Watch Flask app: dashboard, JSON state API, and latest-frame endpoint."""

import os
import threading
import time

import requests
from flask import Flask, Response, jsonify, render_template, request

import narrate
import state

app = Flask(__name__)

CAMERAS_URL = "https://webcams.nyctmc.org/api/cameras"
_CAMERAS_TTL = 300  # seconds
_cameras_lock = threading.Lock()
_cameras_cache: dict = {"ts": 0.0, "data": []}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    snapshot = state.get_snapshot()
    snapshot["history"] = state.get_history()
    return jsonify(snapshot)


@app.route("/frame.jpg")
def frame():
    jpeg = state.get_frame()
    if jpeg is None:
        return "", 204
    return Response(
        jpeg, mimetype="image/jpeg", headers={"Cache-Control": "no-store"}
    )


@app.route("/api/cameras")
def api_cameras():
    now = time.time()
    with _cameras_lock:
        if _cameras_cache["data"] and now - _cameras_cache["ts"] < _CAMERAS_TTL:
            return jsonify(_cameras_cache["data"])
    try:
        resp = requests.get(CAMERAS_URL, timeout=15)
        resp.raise_for_status()
        # The TMC API reports isOnline as the string "true"/"false".
        cams = [
            {"id": c.get("id"), "name": c.get("name"), "area": c.get("area")}
            for c in resp.json()
            if c.get("id") and c.get("isOnline") in (True, "true", "True", 1)
        ]
        cams.sort(key=lambda c: (c["area"] or "", c["name"] or ""))
        with _cameras_lock:
            _cameras_cache["ts"] = now
            _cameras_cache["data"] = cams
        return jsonify(cams)
    except Exception:
        app.logger.exception("camera list fetch failed; returning empty list")
        return jsonify([])


@app.route("/api/camera", methods=["POST"])
def api_set_camera():
    body = request.get_json(silent=True) or {}
    camera_id = body.get("id")
    if not isinstance(camera_id, str) or not camera_id.strip():
        return jsonify({"error": "id must be a non-empty string"}), 400
    try:
        import vision

        vision.set_camera(camera_id.strip())
    except (ImportError, AttributeError):
        return jsonify({"error": "vision module unavailable"}), 503
    return jsonify({"camera": state.get_camera_meta()})


@app.route("/healthz")
def healthz():
    return "ok", 200


# Start background workers at import time so they run under gunicorn too.
narrate.start_narration_loop()

# Soham's vision module (camera polling + Roboflow) may not have landed yet.
try:
    import vision

    vision.start()
except (ImportError, AttributeError):
    pass


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
