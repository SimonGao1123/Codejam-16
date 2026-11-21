"""Audio analysis: splits the 16 kHz float32 mic stream from Electron into 50 ms blocks for the
voice monitor (pitch / volume) and 30 ms frames for the transcriber (Vosk + filler detection).
Message format: docs/audio-protocol.md."""

import threading
import time
from bisect import bisect_right

import numpy as np

from .transcript import FRAME, SR, Transcriber
from .voice import BLOCK_SIZE, BLOCK_SECONDS, VoiceCalibration, VoiceMonitor

VOICE_EVERY_BLOCKS = 2          # send a voice reading every 100 ms
VOICE_RULES = {VoiceMonitor.VOLUME_SPIKE: ("volume", "volume_spike"),
               VoiceMonitor.PITCH_SPIKE: ("pitch", "pitch_spike"),
               VoiceMonitor.QUIET: ("quiet", "quiet")}  # text -> (rule id, VoiceMonitor attribute)


class AudioAnalyzer:
    def __init__(self, vosk_model, whisper_model, alerts):
        self.alerts = alerts                        # shared AlertBoard, shown on the webcam overlay
        self.transcriber = Transcriber(vosk_model, whisper_model)
        self._calibrate_requested = threading.Event()
        self.settings = {}                          # Live alerts rules, see apply_settings()
        self.last_filler_alert = -float("inf")
        self.reset()

    def apply_settings(self, rules):
        """Live alerts settings for the voice rules (volume, pitch, quiet) and filler words."""
        self.settings = rules
        self._configure_voice()

    def _configure_voice(self):
        v = self.voice
        for rule_id, alert_rule, attr in (("volume", v.volume_spike, "volume_jump_db"),
                                          ("pitch", v.pitch_spike, "pitch_jump_semitones"),
                                          ("quiet", v.quiet, "quiet_drop_db")):
            s = self.settings.get(rule_id)
            if s:
                alert_rule.duration_seconds = float(s["duration"])
                alert_rule.cooldown_seconds = float(s["cooldown"])
                if s.get("threshold") is not None:
                    setattr(v, attr, float(s["threshold"]))

    def enabled(self, rule_id):
        return self.settings.get(rule_id, {}).get("enabled", True)

    def reset(self):
        """New practice session: fresh transcript, filler counts and voice baseline."""
        self.voice = VoiceMonitor()
        self._configure_voice()                     # a new monitor keeps the Live alerts settings
        self.voice_calibration = VoiceCalibration(self.voice)
        self.transcriber.reset()
        self.pending = np.zeros(0, dtype=np.float32)
        self.voice_pos = self.speech_pos = 0        # samples consumed by each side
        self.blocks = 0
        self.voice_incidents = {}
        self.voice_checkpoints = []
        self._sample_offsets = []
        self._chunk_times = []
        self._received_samples = 0
        self.has_speech_samples = False
        self.recent_pitches = []                    # every 50 ms block's pitch since the last voice message

    def set_whisper(self, model):
        self.transcriber.whisper = model

    def request_calibration(self):
        """Thread-safe: called from the webcam side when the user clicks Calibrate."""
        self._calibrate_requested.set()

    @property
    def voice_line(self):
        """Status line for the webcam overlay, or None before any audio arrived."""
        v = self.voice
        if v.latest_volume_dbfs is None:
            return None
        if self.voice_calibration.active or self.voice_calibration.message.startswith("Voice: too"):
            return self.voice_calibration.message
        pitch = f"{v.latest_pitch_hz:.0f} Hz" if v.latest_pitch_hz else "-- Hz"
        return f"voice: {pitch}  {v.latest_volume_dbfs:.0f} dBFS"

    def feed(self, samples, started_at=None):
        """float32 samples in -> list of messages for Electron."""
        if started_at is not None and len(samples):
            self._sample_offsets.append(self._received_samples)
            self._chunk_times.append(started_at)
        self._received_samples += len(samples)
        self.pending = np.concatenate([self.pending, samples])
        out = []
        transcript_changed, new_sentences, new_fillers = False, [], []

        if self._calibrate_requested.is_set():
            self._calibrate_requested.clear()
            self.voice_calibration.start(self.blocks)

        # voice: 50 ms blocks
        while self.voice_pos + BLOCK_SIZE <= len(self.pending):
            block = self.pending[self.voice_pos:self.voice_pos + BLOCK_SIZE]
            self.voice_pos += BLOCK_SIZE
            self.blocks += 1
            if self.voice_calibration.active:
                self.voice_calibration.collect(block, self.blocks)
            else:
                alerts = self.voice.process(block, self.blocks * BLOCK_SECONDS)
                self.voice_incidents.setdefault("quiet", 0)
                if self.voice.typical_volume_dbfs is not None:
                    self.voice_incidents.setdefault("volume", 0)
                if self.voice.typical_pitch_hz is not None and self.voice.latest_pitch_hz is not None:
                    self.voice_incidents.setdefault("pitch", 0)
                for alert in alerts:
                    rule, attribute = VOICE_RULES[alert]
                    self.voice_incidents[rule] = self.voice_incidents.get(rule, 0) + 1
                    active_blocks = getattr(self.voice, attribute).active_blocks
                    onset = self._recording_clock(max(0, self.blocks - max(active_blocks, 1)) * BLOCK_SIZE)
                    if onset is not None:
                        self.voice_checkpoints.append((onset, "voice_tone", alert))
                    if self.enabled(rule):
                        self.alerts.post(alert)
            p = self.voice.latest_pitch_hz
            self.recent_pitches.append(round(float(p), 1) if p else None)
            if self.blocks % VOICE_EVERY_BLOCKS == 0:
                out.append(self.voice_message())

        # speech: 30 ms frames of int16 for webrtcvad / Vosk
        while self.speech_pos + FRAME <= len(self.pending):
            frame = self.pending[self.speech_pos:self.speech_pos + FRAME]
            self.speech_pos += FRAME
            pcm = (np.clip(frame, -1, 1) * 32767).astype("<i2").tobytes()
            events = self.transcriber.feed(pcm)
            self.has_speech_samples = True
            transcript_changed |= bool(events)
            self.collect(events, new_sentences, new_fillers)

        # drop what both sides have consumed
        done = min(self.voice_pos, self.speech_pos)
        self.pending = self.pending[done:]
        self.voice_pos -= done
        self.speech_pos -= done

        if transcript_changed:
            out.append(self.transcript_message(new_sentences, new_fillers))
        return out

    def finish(self):
        """End of session: close the open phrase so its words make it into the transcript."""
        sentences, fillers = [], []
        self.collect(self.transcriber.flush(), sentences, fillers)
        return [self.transcript_message(sentences, fillers, partial="")]

    def _recording_clock(self, sample):
        """Convert an analyzer sample offset to the renderer's monotonic clock."""
        index = bisect_right(self._sample_offsets, sample) - 1
        if index < 0:
            return None
        return self._chunk_times[index] + (sample - self._sample_offsets[index]) / SR

    def checkpoints(self):
        result = self.voice_checkpoints.copy()
        for when, word in self.transcriber.filler_checkpoints:
            onset = self._recording_clock(round(when * SR))
            if onset is not None:
                result.append((onset, "filler", f"Filler: {word}"))
        return result

    def collect(self, events, sentences, fillers):
        """Sort transcriber events into new sentences / fillers; fillers also become alerts."""
        for kind, value in events:
            if kind == "sentence":
                sentences.append(value)
            elif kind == "filler":
                fillers.append(value)               # always counted; the alert follows the rule
                cooldown = float(self.settings.get("filler", {}).get("cooldown", 0))
                now = time.monotonic()
                if self.enabled("filler") and now - self.last_filler_alert >= cooldown:
                    self.last_filler_alert = now
                    self.alerts.post(f"Filler: {value}" if value != "filler?" else "Filler?")

    def voice_message(self):
        v, cal = self.voice, self.voice_calibration
        pitches, self.recent_pitches = self.recent_pitches, []
        captured_at = self._recording_clock(self.blocks * BLOCK_SIZE - 1)
        return {
            "type": "voice",
            "captured_at": captured_at,
            "pitch_hz": round(v.latest_pitch_hz, 1) if v.latest_pitch_hz else None,
            "pitches": pitches,                     # one per 50 ms block, oldest first (for the graph)
            "volume_dbfs": round(float(v.latest_volume_dbfs), 1),
            # reference pitch for the graph, and the pitch where a spike alert would fire
            "typical_hz": round(float(v.typical_pitch_hz), 1) if v.typical_pitch_hz else None,
            "spike_hz": (round(float(v.typical_pitch_hz) * 2 ** (v.pitch_jump_semitones / 12), 1)
                         if v.typical_pitch_hz else None),
            "calibrated": v.calibrated_baseline is not None,
            "calibrating": cal.active,
            "message": cal.message,
        }

    def transcript_message(self, new_sentences, new_fillers, partial=None):
        t = self.transcriber
        # live counts: finished sentences plus fillers already heard in the sentence still being spoken
        fillers = {w: n + t.alerted.get(w, 0) for w, n in t.totals.items()}
        return {
            "type": "transcript",
            "new_sentences": new_sentences,
            "partial": t.last_partial if partial is None else partial,
            "new_fillers": new_fillers,
            "fillers": fillers,
            "total": sum(fillers.values()),
        }
