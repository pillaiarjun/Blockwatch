"""Block Watch Flask app: dashboard, JSON state API, and latest-frame endpoint."""

import os

from flask import Flask, Response, jsonify, render_template

import narrate
import state

app = Flask(__name__)


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
