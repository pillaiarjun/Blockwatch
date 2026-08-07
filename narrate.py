"""Gemini narration agent for Block Watch.

Every 60 seconds, summarizes the recent detection history from `state` and asks
Gemini to describe what's happening on the block, storing the result via
state.set_narration(). Runs in a daemon thread started by start_narration_loop().
"""

import logging
import os
import threading
import time

import requests

import state

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("narrate")

INTERVAL_SECONDS = 60


def _build_prompt(history: list) -> str:
    camera = state.get_camera_meta()
    camera_name = camera.get("name") or camera.get("id") or "an NYC intersection"
    # Keep the prompt small: the last ~10 minutes of history is plenty.
    recent = history[-40:]
    lines = [
        f'{entry["ts"]}: '
        + ", ".join(f"{k}={v}" for k, v in sorted(entry["counts"].items()))
        for entry in recent
    ]
    data = "\n".join(lines)
    return (
        f"You are Block Watch, an agent monitoring the NYC DOT traffic camera at "
        f"{camera_name}. Here are object detection counts over the last few "
        f"minutes:\n{data}\n\nIn 2-3 sentences, describe what is happening on "
        f"this block right now and flag anything unusual or noteworthy. Be "
        f"specific and concrete."
    )


def _call_gemini(prompt: str) -> str | None:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.warning("GEMINI_API_KEY not set; skipping narration")
        return None
    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    resp = requests.post(
        url,
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=30,
    )
    if resp.status_code in (400, 404):
        log.error(
            "Gemini rejected model %r (HTTP %s): %s — check GEMINI_MODEL; "
            "keeping previous narration",
            model,
            resp.status_code,
            resp.text[:500],
        )
        return None
    resp.raise_for_status()
    body = resp.json()
    return body["candidates"][0]["content"]["parts"][0]["text"].strip()


def _loop() -> None:
    while True:
        try:
            history = state.get_history()
            if len(history) < 2:
                log.info("not enough history yet (%d entries); skipping", len(history))
            else:
                text = _call_gemini(_build_prompt(history))
                if text:
                    state.set_narration(text)
                    log.info("narration updated: %s", text[:120])
        except Exception:
            log.exception("narration cycle failed; will retry next interval")
        time.sleep(INTERVAL_SECONDS)


def start_narration_loop() -> threading.Thread:
    thread = threading.Thread(target=_loop, name="narration-loop", daemon=True)
    thread.start()
    return thread
