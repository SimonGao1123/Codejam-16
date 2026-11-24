"""Audio analysis: splits the 16 kHz float32 mic stream from Electron into 50 ms blocks for the
voice monitor (pitch / volume) and 30 ms frames for the transcriber (Moonshine + filler detection).
If the user gave a script, the transcriber's text is then compared with it (script_compare.py):
no extra audio processing, just the transcript that's already there.
Message format: docs/audio-protocol.md."""

import time
from bisect import bisect_right
from statistics import median

import numpy as np

from .script_compare import ScriptTracker, chunk_script
from .transcript import FRAME, SR, Transcriber, spoken_words
from .voice import BLOCK_SIZE, BLOCK_SECONDS, VoiceMonitor

VOICE_EVERY_BLOCKS = 2          # send a voice reading every 100 ms
VOICE_RULES = {VoiceMonitor.VOLUME_SPIKE: ("volume", "volume_spike"),
               VoiceMonitor.PITCH_SPIKE: ("pitch", "pitch_spike"),
               VoiceMonitor.QUIET: ("quiet", "quiet"),
               VoiceMonitor.PAUSE: ("pause", "pause")}  # text -> (rule id, VoiceMonitor attribute)
SCRIPT_RULES = {"jump": "out_of_order", "back": "out_of_order", "off_topic": "off_script",
                "stuck": "stuck"}             # script event kind -> Live alerts rule id
SCRIPT_CHECK_S = 1.5            # compare the transcript with the script at most this often
SPEECH_MARGIN_DB = 10           # louder than the room's measured noise by this much = speaking
SILENCE_LIMITS_DBFS = (-70.0, -30.0)   # keep the "not speaking" level sane if the measurement was off
QUIET_SPEECH_HEADROOM_DB = 12   # reserve a range where quiet audio can count without a pitch reading
VOLUME_MIN_SPEECH_S = 5         # volume is scored once there's this much voiced speech
VOLUME_SEGMENT_S = 10           # volume steadiness compares the level of each 10 s of speech
PACE_PAUSE_BREAK_S = 1           # deliberate presentation pauses do not lower the final pace score
LIVE_PACE_TOLERANCE = 0.30       # live pace alert: live WPM this far (as a share) off the desired pace
AVERAGE_PACE_TOLERANCE = 0.15    # average pace alert default (the Live alerts slider is a percentage)
AVERAGE_LINES = 3                # average WPM = the last 3 finished transcript lines, pauses included
DESIRED_WPM_LIMITS = (40.0, 250.0)   # keep a script / time-limit pace sane


class AudioAnalyzer:
    def __init__(self, moonshine_model, alerts):
        self.alerts = alerts                        # shared AlertBoard, shown on the webcam overlay
        self.transcriber = Transcriber(moonshine_model)     # from load_moonshine() in transcript.py
        self.settings = {}                          # Live alerts rules, see apply_settings()
        self.last_alert = {}                        # rule id -> time of its last pop-up (cooldowns)
        self.embedder = None                        # script comparison model, loaded in the background
        self.noise_floor = None                     # room noise (dBFS) measured before the session
        self.script_chunks = []                     # the user's script, split into parts ([] = no script)
        self.script_tracker = None
        self.script_words = 0                       # words in the script, for the desired pace
        self.target_seconds = None                  # time the user has to present (None = not set)
        self.reset()

    def apply_settings(self, rules):
        """Settings for the voice, filler and script rules."""
        self.settings = rules
        self._configure_voice()
        self._configure_silence()
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
        self.script_words = len(spoken_words(text)) if self.script_chunks else 0
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
            if e["kind"] in ("jump", "back"):
                self.script_order_events += 1           # scored: a point off per event
            elif e["kind"] == "off_topic":
                self.script_off_events += 1             # scored: a point off per stretch
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

    def script_match(self):
        """0-1: how much of the script was covered, for the final score.
        None when there's no script, or the comparison model never loaded."""
        t = self.script_tracker
        if not t or not t.chunks:
            return None
        covered = [i for i in range(t.position + 1) if i not in t.skipped]
        return len(covered) / len(t.chunks)
    
    def _configure_voice(self):
        v = self.voice
        for rule_id, alert_rule, attr in (("volume", v.volume_spike, "volume_jump_db"),
                                          ("pitch", v.pitch_spike, "pitch_jump_semitones"),
                                          ("quiet", v.quiet, "quiet_drop_db"),
                                          ("pause", v.pause, None)):
            s = self.settings.get(rule_id)
            if s:
                alert_rule.duration_seconds = float(s["duration"])
                alert_rule.cooldown_seconds = float(s["cooldown"])
                if attr and s.get("threshold") is not None:
                    setattr(v, attr, float(s["threshold"]))

    def enabled(self, rule_id):
        return self.settings.get(rule_id, {}).get("enabled", True)

    def set_noise_floor(self, dbfs):
        """The room's noise level, measured by the app while the user stood still before the
        session (get-ready countdown). Blocks no louder than this + SPEECH_MARGIN_DB count as not
        speaking. None clears it (fixed thresholds again)."""
        self.noise_floor = None if dbfs is None else float(dbfs)
        self._configure_silence()

    def _configure_silence(self):
        if self.noise_floor is None:
            self.voice.silence_dbfs = None
            return
        lo, hi = SILENCE_LIMITS_DBFS
        cutoff = min(hi, max(lo, self.noise_floor + SPEECH_MARGIN_DB))
        quiet_limit = self.voice.quiet_limit_dbfs
        if quiet_limit is not None:
            cutoff = min(cutoff, max(lo, quiet_limit - QUIET_SPEECH_HEADROOM_DB))
        self.voice.silence_dbfs = cutoff

    def reset(self, voice_baseline=None, recording_baseline=False):
        """Start a live session from its saved calibration or an upload from its own audio."""
        self.voice = VoiceMonitor(
            baseline=voice_baseline, recording_baseline=recording_baseline
        )
        self._configure_voice()                     # a new monitor keeps the Settings
        self._configure_silence()                   # and the room's measured silence level
        self.transcriber.reset()
        self.pending = np.zeros(0, dtype=np.float32)
        self.voice_pos = self.speech_pos = 0        # samples consumed by each side
        self.blocks = 0
        self.voice_incidents = {}
        self.speech_volumes = []                    # dBFS of every voiced 50 ms block, for the volume score
        self.voice_checkpoints = []
        self.script_order_events = 0                # skipped ahead / went back, this session
        self.script_off_events = 0                  # stretches matching no part of the script
        self._sample_offsets = []
        self._chunk_times = []
        self._received_samples = 0
        self.has_speech_samples = False
        self.recent_pitches = []                    # every 50 ms block's pitch since the last voice message
        if self.script_tracker:                     # same script, start again from the top
            self.script_tracker.reset()
            self.last_script_text, self.last_script_check = "", -float("inf")

    @property
    def voice_line(self):
        """Status line for the webcam overlay, or None before any audio arrived."""
        v = self.voice
        if v.latest_volume_dbfs is None:
            return None
        pitch = f"{v.latest_pitch_hz:.0f} Hz" if v.latest_pitch_hz else "-- Hz"
        silent = "  (not speaking)" if v.silence_dbfs is not None and not v.speaking else ""
        return f"voice: {pitch}  {v.latest_volume_dbfs:.0f} dBFS{silent}"

    def feed(self, samples, started_at=None):
        """float32 samples in -> list of messages for Electron."""
        if started_at is not None and len(samples):
            self._sample_offsets.append(self._received_samples)
            self._chunk_times.append(started_at)
        self._received_samples += len(samples)
        self.pending = np.concatenate([self.pending, samples])
        out = []
        transcript_changed, new_sentences, new_fillers = False, [], []

        # voice: 50 ms blocks
        while self.voice_pos + BLOCK_SIZE <= len(self.pending):
            block = self.pending[self.voice_pos:self.voice_pos + BLOCK_SIZE]
            self.voice_pos += BLOCK_SIZE
            self.blocks += 1
            alerts = self.voice.process(block, self.blocks * BLOCK_SECONDS)
            if self.voice.typical_volume_dbfs is not None and self.voice.speaking:
                self.voice_incidents.setdefault("quiet", 0)
                self.voice_incidents.setdefault("volume", 0)
            if self.voice.typical_pitch_hz is not None and self.voice.latest_pitch_hz is not None:
                self.voice_incidents.setdefault("pitch", 0)
            if self.voice.has_spoken:
                self.voice_incidents.setdefault("pause", 0)
            if self.voice.latest_pitch_hz is not None:
                self.speech_volumes.append(self.voice.latest_volume_dbfs)
            for alert, (rule, attribute) in VOICE_RULES.items():
                detector = getattr(self.voice, attribute)
                if not detector.new_incident:
                    continue
                self.voice_incidents[rule] = self.voice_incidents.get(rule, 0) + 1
                onset = self._recording_clock((self.blocks - detector.span_blocks) * BLOCK_SIZE)
                if onset is not None:
                    self.voice_checkpoints.append((onset, "volume" if rule == "pause" else "voice_tone", alert))
            for alert in alerts:
                if self.enabled(VOICE_RULES[alert][0]):
                    self.alerts.post(alert)         # voice rules have their own cooldowns
            p = self.voice.latest_pitch_hz
            self.recent_pitches.append(round(float(p), 1) if p else None)
            if self.blocks % VOICE_EVERY_BLOCKS == 0:
                out.append(self.voice_message())

        # speech: 30 ms frames of int16 for the transcriber (Moonshine batches them internally)
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
        if new_sentences:
            self.check_average_pace()
        if transcript_changed:
            out.append(self.transcript_message(new_sentences, new_fillers))
        if self.script_chunks:
            out += self.check_script()
        return out

    def finish(self):
        """End of session: close the open phrase so its words make it into the transcript."""
        recording_incidents = self.voice.finalize_recording_baseline()
        if recording_incidents is not None:
            self.voice_incidents = dict.fromkeys(("quiet", "volume", "pitch"), 0)
            labels = {
                "quiet": VoiceMonitor.QUIET,
                "volume": VoiceMonitor.VOLUME_SPIKE,
                "pitch": VoiceMonitor.PITCH_SPIKE,
            }
            for block, rule in recording_incidents:
                self.voice_incidents[rule] += 1
                onset = self._recording_clock(block * BLOCK_SIZE)
                if onset is not None:
                    self.voice_checkpoints.append((onset, "voice_tone", labels[rule]))
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

    def set_target_time(self, seconds):
        """The time the user has to present (None / 0 = not set) -> pace_target message."""
        try:
            self.target_seconds = float(seconds) if seconds and float(seconds) > 0 else None
        except (TypeError, ValueError):
            self.target_seconds = None
        return self.pace_target_message()

    def desired_wpm(self):
        """The pace to aim for: script words / the time the user has, when both were given;
        otherwise the Settings Target pace slider (default 140)."""
        if self.script_words and self.target_seconds:
            lo, hi = DESIRED_WPM_LIMITS
            return round(min(hi, max(lo, self.script_words * 60 / self.target_seconds)), 1)
        return self.pace_target_wpm()

    def desired_source(self):
        return "script" if self.script_words and self.target_seconds else "setting"

    def pace_target_message(self):
        return {"type": "pace_target", "desired_wpm": self.desired_wpm(), "source": self.desired_source(),
                "script_words": self.script_words, "target_seconds": self.target_seconds}

    def average_wpm(self):
        """Words in the last AVERAGE_LINES finished lines / the time from the first one's start to
        the last one's end, so the pauses between them count. None until there are enough lines."""
        lines = self.transcriber.lines[-AVERAGE_LINES:]
        if len(lines) < AVERAGE_LINES:
            return None
        span = lines[-1][1] - lines[0][0]
        if span < 2:
            return None
        return round(sum(words for _, _, words in lines) * 60 / span, 1)

    def check_average_pace(self):
        """When a line finishes: "Falling behind" / "Too fast" if the average is more than the
        pace_avg threshold (a percentage) off the desired pace."""
        average = self.average_wpm()
        if average is None:
            return
        threshold = self.settings.get("pace_avg", {}).get("threshold")
        tolerance = AVERAGE_PACE_TOLERANCE if threshold is None else float(threshold) / 100
        desired = self.desired_wpm()
        if average < desired * (1 - tolerance):
            self.post_rule("pace_avg", "Falling behind: speed up")
        elif average > desired * (1 + tolerance):
            self.post_rule("pace_avg", "Too fast: slow down")

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
        target = self.desired_wpm()
        if pace < target * (1 - LIVE_PACE_TOLERANCE):
            self.post_rule("pace", "Pick up the pace")
        elif pace > target * (1 + LIVE_PACE_TOLERANCE):
            self.post_rule("pace", "Slow down")

    def _recording_clock(self, sample):
        """Convert an analyzer sample offset to the renderer's monotonic clock."""
        index = bisect_right(self._sample_offsets, sample) - 1
        if index < 0:
            return None
        return self._chunk_times[index] + (sample - self._sample_offsets[index]) / SR

    def volume_summary(self):
        """How loud the speech was, for the volume score: the median level of voiced blocks, the median
        of each VOLUME_SEGMENT_S of speech (steadiness), and the room's noise level measured at the
        start (None if it wasn't). None until there's VOLUME_MIN_SPEECH_S of speech."""
        volumes = self.speech_volumes
        if len(volumes) * BLOCK_SECONDS < VOLUME_MIN_SPEECH_S:
            return None
        size = round(VOLUME_SEGMENT_S / BLOCK_SECONDS)
        segments = [median(volumes[i:i + size]) for i in range(0, len(volumes) - size + 1, size)]
        return {
            "speech_dbfs": round(float(median(volumes)), 1),
            "segment_dbfs": [round(float(s), 1) for s in segments],
            "room_dbfs": None if self.noise_floor is None else round(self.noise_floor, 1),
        }

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
                self.post_rule("filler", f"Filler: {value}")

    def voice_message(self):
        v = self.voice
        pitches, self.recent_pitches = self.recent_pitches, []
        captured_at = self._recording_clock(self.blocks * BLOCK_SIZE - 1)
        pitch_times = [
            self._recording_clock(block * BLOCK_SIZE - 1)
            for block in range(self.blocks - len(pitches) + 1, self.blocks + 1)
        ]
        return {
            "type": "voice",
            "alerts": self.alerts.active(),
            "captured_at": captured_at,
            "pitch_hz": round(v.latest_pitch_hz, 1) if v.latest_pitch_hz else None,
            "pitches": pitches,                     # one per 50 ms block, oldest first (for the graph)
            "pitch_times": pitch_times,
            "wpm": self.live_wpm(),                 # provisional estimate for the overlay
            "volume_dbfs": round(float(v.latest_volume_dbfs), 1),
            # reference pitch for the graph, and the pitch where a spike alert would fire
            "typical_hz": round(float(v.typical_pitch_hz), 1) if v.typical_pitch_hz else None,
            "typical_volume_dbfs": v.typical_volume_dbfs,
            "baseline_source": v.baseline_source,
            "quiet_limit_dbfs": v.quiet_limit_dbfs,
            "spike_hz": (round(float(v.typical_pitch_hz) * 2 ** (v.pitch_jump_semitones / 12), 1)
                         if v.typical_pitch_hz else None),
            "speaking": bool(v.speaking),           # detectable pitch or audio above the room cutoff
            "silence_dbfs": None if v.silence_dbfs is None else round(float(v.silence_dbfs), 1),

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
            "wpm": self.live_wpm(),                 # recent rate incl. the line still being spoken
            "avg_wpm": self.average_wpm(),          # last 3 finished lines, pauses included
            "desired_wpm": self.desired_wpm(),
            "desired_source": self.desired_source(),
        }
