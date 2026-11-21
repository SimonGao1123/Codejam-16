"""Audio analysis: splits the 16 kHz float32 mic stream from Electron into 50 ms blocks for the
voice monitor (pitch / volume) and 30 ms frames for the transcriber (Vosk + filler detection).
If the user gave a script, the transcriber's text is then compared with it (script_compare.py):
no extra audio processing, just the transcript that's already there.
Message format: docs/audio-protocol.md."""

import threading
import time
from bisect import bisect_right

import numpy as np

from .script_compare import ScriptTracker, chunk_script
from .transcript import FRAME, SR, Transcriber
from .voice import BLOCK_SIZE, BLOCK_SECONDS, VoiceCalibration, VoiceMonitor

VOICE_EVERY_BLOCKS = 2          # send a voice reading every 100 ms
VOICE_RULES = {VoiceMonitor.VOLUME_SPIKE: ("volume", "volume_spike"),
               VoiceMonitor.PITCH_SPIKE: ("pitch", "pitch_spike"),
               VoiceMonitor.QUIET: ("quiet", "quiet")}  # text -> (rule id, VoiceMonitor attribute)
SCRIPT_RULES = {"jump": "out_of_order", "back": "out_of_order", "off_topic": "off_script",
                "stuck": "stuck"}             # script event kind -> Live alerts rule id
SCRIPT_CHECK_S = 1.5            # compare the transcript with the script at most this often
PACE_COMFORT_BAND_WPM = 40       # matches the scoring rubric's full-credit band
PACE_PAUSE_BREAK_S = 1           # deliberate presentation pauses do not lower the final pace score


class AudioAnalyzer:
    def __init__(self, vosk_model, whisper_model, alerts):
        self.alerts = alerts                        # shared AlertBoard, shown on the webcam overlay
        self.transcriber = Transcriber(vosk_model, whisper_model)
        self._calibrate_requested = threading.Event()
        self.settings = {}                          # Live alerts rules, see apply_settings()
        self.last_alert = {}                        # rule id -> time of its last pop-up (cooldowns)
        self.embedder = None                        # script comparison model, loaded in the background
        self.script_chunks = []                     # the user's script, split into parts ([] = no script)
        self.script_tracker = None
        self.reset()

    def apply_settings(self, rules):
        """Live alerts settings for the voice, filler and script rules."""
        self.settings = rules
        self._configure_voice()
        self._configure_script()

    def _configure_script(self):
        t = self.script_tracker
        if not t:
            return
        off, stuck = self.settings.get("off_script", {}), self.settings.get("stuck", {})
        if off.get("threshold") is not None:
            t.threshold = float(off["threshold"]) / 100        # slider is "% match needed"
        if off.get("duration") is not None:
            t.off_topic_s = float(off["duration"])
        if stuck.get("threshold") is not None:
            t.stuck_factor = float(stuck["threshold"])

    def post_rule(self, rule_id, text):
        """Show a pop-up for a Live alerts rule, unless it's turned off or still cooling down."""
        cooldown = float(self.settings.get(rule_id, {}).get("cooldown", 0))
        now = time.monotonic()
        if self.enabled(rule_id) and now - self.last_alert.get(rule_id, -float("inf")) >= cooldown:
            self.last_alert[rule_id] = now
            self.alerts.post(text)

    # ---------- script comparison ----------
    def set_embedder(self, embedder):
        """Called from the loader thread; the tracker itself is built on the audio thread."""
        self.embedder = embedder

    def set_script(self, text):
        """The user's script (None / empty = no script, comparison off) -> script_loaded message."""
        self.script_chunks = chunk_script(text) if text and text.strip() else []
        self.script_tracker = None
        self._build_tracker()
        return self.script_loaded_message()

    def _build_tracker(self):
        if self.script_chunks and self.embedder and not self.script_tracker:
            self.script_tracker = ScriptTracker(self.script_chunks, self.embedder)
            self._configure_script()
            self.last_script_text, self.last_script_check = "", -float("inf")
            return True
        return False

    def script_loaded_message(self):
        return {"type": "script_loaded", "chunks": self.script_chunks,
                "ready": self.script_tracker is not None}

    def check_script(self, force=False):
        """Compare the transcript so far with the script (at most every SCRIPT_CHECK_S, and only when
        new words arrived) -> list of messages."""
        out = []
        if self._build_tracker():                   # the model finished loading after the script came
            out.append(self.script_loaded_message())
        t = self.script_tracker
        if not t:
            return out
        tr = self.transcriber
        text = " ".join(tr.sentences + [tr.last_partial]).strip()
        now = time.monotonic()
        if text == self.last_script_text or (not force and now - self.last_script_check < SCRIPT_CHECK_S):
            return out
        first = self.last_script_text == ""
        self.last_script_text, self.last_script_check = text, now
        if first and len(text.split()) > 12:
            t.catch_up(text, now)                   # tracking started after the speaker did
        m = t.update(text, now)
        if m is None:
            return out
        for e in m.events:
            self.post_rule(SCRIPT_RULES[e["kind"]], e["cue"])
        out.append({
            "type": "script_position",
            "position": m.position,
            "best": m.best,
            "status": m.status,
            "skipped": sorted(t.skipped),
            "best_score": round(float(m.score[m.best]), 3),
            "events": [e["message"] for e in m.events],
        })
        return out

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
        if self.script_tracker:                     # same script, start again from the top
            self.script_tracker.reset()
            self.last_script_text, self.last_script_check = "", -float("inf")

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
                        self.alerts.post(alert)     # voice rules have their own cooldowns
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

        self.check_pace()
        if transcript_changed:
            out.append(self.transcript_message(new_sentences, new_fillers))
        if self.script_chunks:
            out += self.check_script()
        return out

    def finish(self):
        """End of session: close the open phrase so its words make it into the transcript."""
        sentences, fillers = [], []
        self.collect(self.transcriber.flush(), sentences, fillers)
        return [self.transcript_message(sentences, fillers, partial="")] + self.check_script(force=True)

    def pace_summary(self):
        """Return speaking-rate data for the final score, or None when it is too short.

        A new speaking run starts after a two-second gap.  That keeps natural pauses
        between slides or ideas from making an otherwise measured delivery look slow.
        Each run is split into roughly 20-word samples for the consistency check.
        """
        words = self.transcriber.word_times
        if len(words) < 20:
            return None

        runs, run = [], [words[0]]
        for word in words[1:]:
            if word[0] - run[-1][1] > PACE_PAUSE_BREAK_S:
                runs.append(run)
                run = [word]
            else:
                run.append(word)
        runs.append(run)

        def wpm(part):
            duration = part[-1][1] - part[0][0]
            return len(part) * 60 / duration if duration >= 1 else None

        durations = [part[-1][1] - part[0][0] for part in runs]
        active_seconds = sum(durations)
        if active_seconds < 8:
            return None
        average = len(words) * 60 / active_seconds
        samples = []
        for part in runs:
            for i in range(0, len(part), 20):
                sample = part[i:i + 20]
                # A short final fragment is too noisy to judge consistency.
                if len(sample) >= 8 and (rate := wpm(sample)) is not None:
                    samples.append(rate)
        return {
            "average_wpm": round(average, 1),
            "segment_wpms": [round(rate, 1) for rate in samples],
            "word_count": len(words),
        }

    def current_wpm(self):
        """Recent WPM for the renderer's live stat, based on up to 20 timed words."""
        words = self.transcriber.word_times
        if len(words) < 3:
            return None
        # Do not carry a slide-change pause into the live pace readout.
        start = max(0, len(words) - 20)
        for i in range(len(words) - 1, start, -1):
            if words[i][0] - words[i - 1][1] > 2:
                start = i
                break
        recent = words[start:]
        duration = recent[-1][1] - recent[0][0]
        if len(recent) < 3 or duration < 1.5:
            return None
        return round(len(recent) * 60 / duration, 1)

    def live_wpm(self):
        """Fast Vosk-partial estimate for the overlay; None while the speaker is paused."""
        return self.transcriber.live_wpm()

    def pace_target_wpm(self):
        """Preferred WPM from the shared settings, with a safe default."""
        target = self.settings.get("pace", {}).get("threshold", 140)
        try:
            return min(200, max(80, float(target)))
        except (TypeError, ValueError):
            return 140.0

    def check_pace(self):
        """Post a live cue after each finalized transcript update when pace is out of range."""
        # A short, responsive window makes the cue useful while the speaker can
        # still adjust. It does not run during silence (live_wpm returns None).
        pace = self.transcriber.live_wpm(minimum_seconds=3, window_seconds=5)
        if pace is None:
            # Keep a fallback for timing-only callers/tests; real sessions have
            # pace_history, and must not alert while the speaker is paused.
            if self.transcriber.pace_history:
                return
            summary = self.pace_summary()
            if summary is None:
                return
            pace = summary["average_wpm"]
        target = self.pace_target_wpm()
        if pace < target - PACE_COMFORT_BAND_WPM:
            self.post_rule("pace", "Pick up the pace")
        elif pace > target + PACE_COMFORT_BAND_WPM:
            self.post_rule("pace", "Slow down")

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
                fillers.append(value)               # always counted; the pop-up follows the rule
                self.post_rule("filler", f"Filler: {value}" if value != "filler?" else "Filler?")

    def voice_message(self):
        v, cal = self.voice, self.voice_calibration
        pitches, self.recent_pitches = self.recent_pitches, []
        captured_at = self._recording_clock(self.blocks * BLOCK_SIZE - 1)
        return {
            "type": "voice",
            "captured_at": captured_at,
            "pitch_hz": round(v.latest_pitch_hz, 1) if v.latest_pitch_hz else None,
            "pitches": pitches,                     # one per 50 ms block, oldest first (for the graph)
            "wpm": self.live_wpm(),                 # provisional Vosk-partial estimate for the overlay
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
            "wpm": self.live_wpm(),
        }
