"""Thread-safe in-memory store shared by the vision and narration halves of Block Watch.

Contract (all functions are safe to call from any thread):

    set_frame(jpeg_bytes: bytes) -> None   # store the latest camera frame (raw JPEG bytes)
    get_frame() -> bytes | None            # latest frame, or None if none yet

    set_counts(counts: dict) -> None       # e.g. {"car": 4, "person": 2, "truck": 1}
                                           # also appends {"ts": iso8601, "counts": ...}
                                           # to the history (capped at last 400 entries)

    set_camera_meta(meta: dict) -> None    # {"id", "name", "latitude", "longitude"}
    get_camera_meta() -> dict

    set_narration(text: str) -> None       # latest Gemini narration

    get_snapshot() -> dict                 # {"counts", "narration", "camera",
                                           #  "last_updated", "history_len"}
    get_history() -> list                  # list of {"ts": iso8601, "counts": {...}}

    reset_for_camera_change(meta: dict) -> None
                                           # atomically clears frame, counts,
                                           # history, and narration, then sets
                                           # camera meta for the new camera
"""

import threading
from datetime import datetime, timezone

_HISTORY_MAX = 400

_lock = threading.Lock()

_frame: bytes | None = None
_counts: dict = {}
_camera_meta: dict = {}
_narration: str = ""
_status: str = ""  # latest vision-loop error, "" when healthy
_last_updated: str | None = None
_history: list = []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_frame(jpeg_bytes: bytes) -> None:
    global _frame, _last_updated
    with _lock:
        _frame = jpeg_bytes
        _last_updated = _now_iso()


def get_frame() -> bytes | None:
    with _lock:
        return _frame


def set_counts(counts: dict) -> None:
    global _counts, _last_updated
    with _lock:
        _counts = dict(counts)
        _last_updated = _now_iso()
        _history.append({"ts": _last_updated, "counts": dict(counts)})
        if len(_history) > _HISTORY_MAX:
            del _history[: len(_history) - _HISTORY_MAX]


def set_camera_meta(meta: dict) -> None:
    global _camera_meta
    with _lock:
        _camera_meta = dict(meta)


def get_camera_meta() -> dict:
    with _lock:
        return dict(_camera_meta)


def set_narration(text: str) -> None:
    global _narration
    with _lock:
        _narration = text


def set_status(text: str) -> None:
    global _status
    with _lock:
        _status = text


def get_snapshot() -> dict:
    with _lock:
        return {
            "counts": dict(_counts),
            "narration": _narration,
            "camera": dict(_camera_meta),
            "status": _status,
            "last_updated": _last_updated,
            "history_len": len(_history),
        }


def get_history() -> list:
    with _lock:
        return [dict(entry) for entry in _history]


def reset_for_camera_change(meta: dict) -> None:
    global _frame, _counts, _camera_meta, _narration, _status, _last_updated
    with _lock:
        _frame = None
        _counts = {}
        _history.clear()
        _narration = ""
        _status = ""
        _last_updated = None
        _camera_meta = dict(meta)
