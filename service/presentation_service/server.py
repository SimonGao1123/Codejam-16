"""
Local WebSocket service the Electron app connects to. One port, two paths:

  ws://127.0.0.1:8766/webcam   landmarks in  -> overlay state out   (docs/webcam-protocol.md)
  ws://127.0.0.1:8766/audio    mic audio in  -> voice + transcript  (docs/audio-protocol.md)

Both share one Session (it's a single-user desktop app): voice / filler / script alerts show up
as banners on the webcam overlay. Calibration happens once at the start of each session (posture
and resting hands from the webcam; the room's silence level is sent on the audio side).
"""

import asyncio
import json
import logging
import math
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from websockets.asyncio.server import serve

from .audio_analysis import AudioAnalyzer
from .scoring import score_presentation
from .script_compare import Embedder
from .transcript import load_moonshine
from .voice import calibrate_voice, parse_voice_baseline
from .webcam_analysis import WebcamAnalyzer

log = logging.getLogger("presentation_service")

ALERT_SHOW_S = 2.5          # how long a voice / filler alert banner stays up
AUDIO_TIMESTAMP = struct.Struct("<d")


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
    def __init__(self, moonshine_model):
        self.alerts = AlertBoard()
        self.webcam = WebcamAnalyzer()
        self.audio = AudioAnalyzer(moonshine_model, self.alerts)
        self.scoring_client = None
        # audio work (Moonshine, voice analysis) runs on one worker thread, in order, off the event loop
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
            session.webcam.calibrate(msg.get("t"), msg.get("delay"), msg.get("duration"),
                                     when_ready=bool(msg.get("when_ready")),
                                     request_id=msg.get("request_id"))
        elif msg["type"] == "settings":
            session.apply_settings(msg["rules"])
        elif msg["type"] == "flush":
            # WebSocket messages are processed in order. This acknowledgement means every frame
            # sent before `flush` has updated the analyzer before the audio side scores it.
            await ws.send(json.dumps({"type": "flushed"}))
    log.info("webcam client disconnected")


async def audio_connection(ws, session):
    log.info("audio client connected")
    loop = asyncio.get_running_loop()
    run = lambda fn, *args: loop.run_in_executor(session.audio_worker, fn, *args)  # noqa: E731
    await run(session.audio.reset)
    started_at = None
    started_monotonic = None                             # server-side clock for the session length
    try:
        async for raw in ws:
            if isinstance(raw, bytes):                   # float32 little-endian, 16 kHz mono
                offset = AUDIO_TIMESTAMP.size if started_at is not None else 0
                if len(raw) <= offset or (len(raw) - offset) % 4:
                    continue
                chunk_start = AUDIO_TIMESTAMP.unpack_from(raw)[0] if offset else None
                if chunk_start is not None and not math.isfinite(chunk_start):
                    continue
                samples = np.frombuffer(raw, dtype="<f4", offset=offset)
                for msg in await run(session.audio.feed, samples, chunk_start):
                    await ws.send(json.dumps(msg))
                continue
            msg = json.loads(raw)
            if msg["type"] == "session_start":
                started_at = msg.get("started_at")
                started_monotonic = time.monotonic()
                session.scoring_client = ws
                session.webcam.start_scoring(started_at)
                await run(
                    session.audio.reset,
                    parse_voice_baseline(msg.get("voice_baseline")),
                    bool(msg.get("recording_baseline")),
                )
            elif msg["type"] == "audio_sync":
                # Uploads send this directly after a binary audio chunk and wait for the reply.
                # WebSocket ordering means its preceding chunk has gone through AudioAnalyzer.feed
                # before this acknowledgement is returned. This prevents a fast upload from
                # outrunning Moonshine's streaming transcription pipeline.
                await ws.send(json.dumps({"type": "audio_processed", "id": msg.get("id")}))
            elif msg["type"] == "session_end" and session.scoring_client is ws:
                session.scoring_client = None
                session.webcam.scoring = False
                body_incidents = session.webcam.incidents.copy()
                body_checkpoints = session.webcam.checkpoints.copy()
                disabled_alerts = {
                    rule_id for rule_id in session.audio.settings if not session.audio.enabled(rule_id)
                }
                # length of the presentation, used by the scorer to scale deductions
                duration_seconds = msg.get("duration_seconds")
                if duration_seconds is None and started_monotonic is not None:
                    duration_seconds = time.monotonic() - started_monotonic

                def finish_and_score():
                    messages = session.audio.finish()
                    transcript = messages[0]
                    voice_incidents = {
                        k: v for k, v in session.audio.voice_incidents.items()
                        if session.audio.enabled(k)
                    }
                    scored_body_incidents = {
                        k: v for k, v in body_incidents.items()
                        if session.webcam.rules.get(k) and session.webcam.rules[k].enabled
                    }
                    score_fillers = (
                        transcript["fillers"]
                        if session.audio.has_speech_samples and session.audio.enabled("filler")
                        else None
                    )
                    score_pace = (
                        session.audio.pace_summary()
                        if session.audio.enabled("pace") or session.audio.enabled("pace_avg")
                        else None
                    )
                    score_script = (
                        session.audio.script_match()
                        if session.audio.enabled("off_script")
                        else None
                    )

                    report = score_presentation(
                        filler_counts=score_fillers,
                        voice_incidents=voice_incidents,
                        body_incidents=scored_body_incidents,
                        pace=score_pace,
                        pace_target_wpm=session.audio.desired_wpm(),
                        duration_seconds=duration_seconds,
                        expected_minutes=(session.audio.target_seconds / 60
                                          if session.audio.target_seconds else None),
                        volume=session.audio.volume_summary(),
                        script_match=session.audio.script_match(),
                        disabled_alerts=disabled_alerts,
                    )
                    checkpoints = []
                    if started_at is not None:
                        checkpoints = [
                            {"at": round(when - started_at, 3), "category": category, "label": label}
                            for when, category, label in sorted(body_checkpoints + session.audio.checkpoints())
                            if when >= started_at
                        ]
                    return [*messages, {"type": "session_score", **report, "checkpoints": checkpoints}]

                # for out in await run(finish_and_score):
                #     await ws.send(json.dumps(out))
                results = await run(finish_and_score)
                score_msg = results[-1]
                log.info("session length: %.2f min, scoring_minutes: %s, source: %s, score: %s",
                         score_msg.get("duration_minutes") or 0,
                         score_msg.get("scoring_minutes"),
                         score_msg.get("duration_source"),
                         score_msg.get("score"))
                for out in results:
                    await ws.send(json.dumps(out))
                    
            elif msg["type"] == "noise_floor":
                await run(session.audio.set_noise_floor, msg.get("dbfs"))
                if msg.get("dbfs") is None:
                    log.info("room noise measurement cleared")
                else:
                    log.info("room noise %.1f dBFS: not speaking below %.1f dBFS", msg["dbfs"],
                             session.audio.voice.silence_dbfs)
            elif msg["type"] == "script":
                await ws.send(json.dumps(await run(session.audio.set_script, msg.get("text"))))
                await ws.send(json.dumps(await run(session.audio.pace_target_message)))   # word count changed
            elif msg["type"] == "target_time":
                await ws.send(json.dumps(await run(session.audio.set_target_time, msg.get("seconds"))))
            elif msg["type"] == "settings":
                session.apply_settings(msg["rules"])
    finally:
        if session.scoring_client is ws:
            session.scoring_client = None
            session.webcam.scoring = False
        log.info("audio client disconnected")


async def voice_calibration_connection(ws):
    async for raw in ws:
        try:
            if not isinstance(raw, bytes) or len(raw) % 4:
                raise ValueError("The microphone sample was incomplete. Try again.")
            baseline = await asyncio.to_thread(calibrate_voice, np.frombuffer(raw, dtype="<f4"))
            await ws.send(json.dumps({"baseline": baseline}))
        except ValueError as error:
            await ws.send(json.dumps({"error": str(error)}))
        return


async def main(host="127.0.0.1", port=8766):
    # Moonshine runs fully on-device; the first run downloads the model, so it can take a while
    log.info("loading Moonshine model (downloaded on first run)")
    session = Session(load_moonshine())

    # the script comparison model (all-MiniLM-L6-v2, ~90 MB download on first run), also in the
    # background; a script that arrives before it's ready starts being tracked once it is
    def load_embedder():
        try:
            session.audio.set_embedder(Embedder())
            log.info("script comparison model ready")
        except Exception as err:
            log.warning("script comparison unavailable (%s)", err)
    threading.Thread(target=load_embedder, daemon=True).start()

    async def handler(ws):
        path = ws.request.path
        if path in ("/webcam", "/"):
            await webcam_connection(ws, session)
        elif path == "/audio":
            await audio_connection(ws, session)
        elif path == "/voice-calibration":
            await voice_calibration_connection(ws)
        else:
            await ws.close(4404, f"unknown path {path}")

    async with serve(handler, host, port, max_size=2**22):
        log.info("listening on ws://%s:%d  (/webcam, /audio)", host, port)
        await asyncio.Future()
