"""Audio analysis: splits the 16 kHz float32 mic stream from Electron into 50 ms blocks for the
voice monitor (pitch / volume) and 30 ms frames for the transcriber (Vosk + filler detection).
Message format: docs/audio-protocol.md."""

import threading

import numpy as np

from .transcript import FRAME, Transcriber
from .voice import BLOCK_SIZE, BLOCK_SECONDS, VoiceCalibration, VoiceMonitor

VOICE_EVERY_BLOCKS = 2          # send a voice reading every 100 ms


class AudioAnalyzer:
    def __init__(self, vosk_model, whisper_model, alerts):
        self.alerts = alerts                        # shared AlertBoard, shown on the webcam overlay
        self.transcriber = Transcriber(vosk_model, whisper_model)
        self._calibrate_requested = threading.Event()
        self.reset()

    def reset(self):
        """New practice session: fresh transcript, filler counts and voice baseline."""
        self.voice = VoiceMonitor()
        self.voice_calibration = VoiceCalibration(self.voice)
        self.transcriber.reset()
        self.pending = np.zeros(0, dtype=np.float32)
        self.voice_pos = self.speech_pos = 0        # samples consumed by each side
        self.blocks = 0

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

    def feed(self, samples):
        """float32 samples in -> list of messages for Electron."""
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
                for alert in self.voice.process(block, self.blocks * BLOCK_SECONDS):
                    self.alerts.post(alert)
            if self.blocks % VOICE_EVERY_BLOCKS == 0:
                out.append(self.voice_message())

        # speech: 30 ms frames of int16 for webrtcvad / Vosk
        while self.speech_pos + FRAME <= len(self.pending):
            frame = self.pending[self.speech_pos:self.speech_pos + FRAME]
            self.speech_pos += FRAME
            pcm = (np.clip(frame, -1, 1) * 32767).astype("<i2").tobytes()
            events = self.transcriber.feed(pcm)
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

    def collect(self, events, sentences, fillers):
        """Sort transcriber events into new sentences / fillers; fillers also become alerts."""
        for kind, value in events:
            if kind == "sentence":
                sentences.append(value)
            elif kind == "filler":
                fillers.append(value)
                self.alerts.post(f"Filler: {value}" if value != "filler?" else "Filler?")

    def voice_message(self):
        v, cal = self.voice, self.voice_calibration
        return {
            "type": "voice",
            "pitch_hz": round(v.latest_pitch_hz, 1) if v.latest_pitch_hz else None,
            "volume_dbfs": round(float(v.latest_volume_dbfs), 1),
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
