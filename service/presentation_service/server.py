"""
Local WebSocket service the Electron app connects to. One port, two paths:

  ws://127.0.0.1:8766/webcam   landmarks in  -> overlay state out   (docs/webcam-protocol.md)
  ws://127.0.0.1:8766/audio    mic audio in  -> voice + transcript  (docs/audio-protocol.md)

Both share one Session (it's a single-user desktop app): clicking Calibrate on the webcam side
also calibrates the voice, and voice / filler alerts show up as banners on the webcam overlay.
"""

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from websockets.asyncio.server import serve

from .audio_analysis import AudioAnalyzer
from .transcript import load_vosk, load_whisper
from .webcam_analysis import WebcamAnalyzer

log = logging.getLogger("presentation_service")

REPO = Path(__file__).resolve().parents[2]
VOSK_MODEL = REPO / "models" / "vosk-model-en-us-0.22"
ALERT_SHOW_S = 2.5          # how long a voice / filler alert banner stays up


class AlertBoard:
    """Alerts posted by the audio side, shown on the webcam overlay for ALERT_SHOW_S. Thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self._until = {}

    def post(self, text):
        with self._lock:
            self._until[text] = time.monotonic() + ALERT_SHOW_S

    def active(self):
        now = time.monotonic()
        with self._lock:
            self._until = {k: v for k, v in self._until.items() if v > now}
            return list(self._until)


class Session:
    def __init__(self, vosk_model):
        self.alerts = AlertBoard()
        self.webcam = WebcamAnalyzer()
        self.audio = AudioAnalyzer(vosk_model, None, self.alerts)
        # audio work (Vosk, Whisper) runs on one worker thread, in order, off the event loop
        self.audio_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio")

    def apply_settings(self, rules):
        """Live alerts settings from the app: {rule id: {enabled, threshold, duration, cooldown}}."""
        self.webcam.apply_settings(rules)
        self.audio.apply_settings(rules)
        log.info("alert settings updated (%d rules, %d off)", len(rules),
                 sum(not r.get("enabled", True) for r in rules.values()))


async def webcam_connection(ws, session):
    log.info("webcam client connected")
    async for raw in ws:
        msg = json.loads(raw)
        if msg["type"] == "frame":
            voice = session.audio.voice_line
            state = session.webcam.process(
                msg,
                extra_lines=[{"text": voice, "color": "white"}] if voice else [],
                extra_alerts=session.alerts.active(),
            )
            await ws.send(json.dumps(state))
        elif msg["type"] == "calibrate":
            session.webcam.calibrate(msg.get("t"))
            session.audio.request_calibration()
        elif msg["type"] == "settings":
            session.apply_settings(msg["rules"])
    log.info("webcam client disconnected")


async def audio_connection(ws, session):
    log.info("audio client connected")
    loop = asyncio.get_running_loop()
    run = lambda fn, *args: loop.run_in_executor(session.audio_worker, fn, *args)  # noqa: E731
    await run(session.audio.reset)
    async for raw in ws:
        if isinstance(raw, bytes):                   # float32 little-endian, 16 kHz mono
            for msg in await run(session.audio.feed, np.frombuffer(raw, dtype="<f4")):
                await ws.send(json.dumps(msg))
            continue
        msg = json.loads(raw)
        if msg["type"] == "session_start":
            await run(session.audio.reset)
        elif msg["type"] == "session_end":
            for out in await run(session.audio.finish):
                await ws.send(json.dumps(out))
        elif msg["type"] == "calibrate":
            session.audio.request_calibration()
    log.info("audio client disconnected")


async def main(host="127.0.0.1", port=8766, whisper_model="tiny.en"):
    log.info("loading Vosk model from %s", VOSK_MODEL)
    session = Session(load_vosk(VOSK_MODEL))

    if whisper_model:
        # Whisper only double-checks suspected um/uh; load it in the background so the service
        # starts right away (the first run downloads the model, ~75 MB for tiny.en)
        def load():
            try:
                session.audio.set_whisper(load_whisper(whisper_model))
                log.info("Whisper %s ready (filler second opinion on)", whisper_model)
            except Exception as err:                 # offline on first run, etc.
                log.warning("Whisper unavailable (%s); suspected fillers will show as [um?]", err)
        threading.Thread(target=load, daemon=True).start()

    async def handler(ws):
        path = ws.request.path
        if path in ("/webcam", "/"):
            await webcam_connection(ws, session)
        elif path == "/audio":
            await audio_connection(ws, session)
        else:
            await ws.close(4404, f"unknown path {path}")

    async with serve(handler, host, port, max_size=2**22):
        log.info("listening on ws://%s:%d  (/webcam, /audio)", host, port)
        await asyncio.Future()
