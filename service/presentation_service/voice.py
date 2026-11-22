"""Voice pitch spikes, volume spikes and sustained low volume, on 50 ms blocks of 16 kHz audio.
VoiceMonitor is from experiments/gap_heuristic/voice_pitch.py.

Pitch and volume are compared against a fixed baseline from a separate microphone calibration.
The room's silence level is still measured at the start of each session."""

from dataclasses import dataclass
from statistics import median

import numpy as np


SAMPLE_RATE = 16_000
BLOCK_SECONDS = 0.05
BLOCK_SIZE = round(SAMPLE_RATE * BLOCK_SECONDS)
CALIBRATION_SECONDS = 6
CALIBRATION_HOP = SAMPLE_RATE // 50
MIN_CALIBRATION_WINDOWS = 30  # about 0.6 s of pitched syllables, spread across the sample
MIN_CALIBRATION_DBFS = -70.0
DEFAULT_NOISE_FLOOR_DBFS = -60.0
QUIET_PITCH_FLOOR_DBFS = -80.0
QUIET_GAP_BLOCKS = round(0.75 / BLOCK_SECONDS)
# a pause ends only after this much continuous voiced audio, so a click, cough or noise blip just
# above the silence level doesn't reset the long-pause timer
SPEECH_RESUME_BLOCKS = 3          # 150 ms
PITCH_FRAME_SIZE = round(SAMPLE_RATE * 0.04)
MIN_PITCH_LAG = SAMPLE_RATE // 450
MAX_PITCH_LAG = SAMPLE_RATE // 70
PITCH_PEAK_TOLERANCE = 0.02
HARMONIC_CONFIRM_BLOCKS = 3
# Require enough voiced material before a recording can receive a tone score.
RECORDING_MIN_VOICED_BLOCKS = 30  # 1.5 s at 50 ms per block


def pitch_hz(samples, *, min_confidence=0.62):
    """Estimate pitch from the first strong autocorrelation peak."""
    start = (len(samples) - PITCH_FRAME_SIZE) // 2
    frame = samples[start:start + PITCH_FRAME_SIZE].astype(np.float64)
    frame -= frame.mean()
    if not np.any(frame):
        return None

    correlation = np.correlate(frame, frame, mode="full")[len(frame) - 1:]
    lags = np.arange(MIN_PITCH_LAG - 1, MAX_PITCH_LAG + 2)
    energy = np.r_[0, np.cumsum(frame ** 2)]
    norms = np.sqrt(energy[len(frame) - lags] * (energy[-1] - energy[lags]))
    scores = np.divide(correlation[lags], norms, out=np.zeros_like(norms), where=norms > 0)
    peaks = np.flatnonzero((scores[1:-1] > scores[:-2]) & (scores[1:-1] >= scores[2:])) + 1
    if not peaks.size or scores[peaks].max() < min_confidence:
        return None

    # Prefer shorter periods only when they explain the waveform almost as well as the best
    # peak. A prominent harmonic can create a strong but distinctly weaker early peak.
    cutoff = max(min_confidence, scores[peaks].max() - PITCH_PEAK_TOLERANCE)
    best = peaks[scores[peaks] >= cutoff][0]
    return SAMPLE_RATE / lags[best]


def volume_dbfs(samples):
    rms = np.sqrt(np.mean(np.square(samples, dtype=np.float64)))
    return float(20 * np.log10(max(rms, 1e-8)))


def calibrate_voice(samples):
    """Measure the fixed reference from pitched syllables in a normal speaking sample."""
    if len(samples) != CALIBRATION_SECONDS * SAMPLE_RATE or not np.all(np.isfinite(samples)):
        raise ValueError("The microphone sample was incomplete. Try again.")
    pitches, volumes = [], []
    for offset in range(0, len(samples) - BLOCK_SIZE + 1, CALIBRATION_HOP):
        block = samples[offset:offset + BLOCK_SIZE]
        volume = volume_dbfs(block)
        if volume < MIN_CALIBRATION_DBFS:
            continue
        pitch = pitch_hz(block)
        if pitch is not None:
            pitches.append(pitch)
            volumes.append(volume)
    if len(pitches) < MIN_CALIBRATION_WINDOWS:
        if volume_dbfs(samples) < MIN_CALIBRATION_DBFS:
            raise ValueError("The microphone level was too low. Check your selected microphone and input volume.")
        raise ValueError("Couldn’t measure a clear pitch. Start speaking when you see “Speak now” and try again.")
    return {"pitch_hz": round(median(pitches), 1), "volume_dbfs": round(median(volumes), 1)}


def parse_voice_baseline(value):
    if not isinstance(value, dict):
        return None
    pitch, volume = value.get("pitch_hz"), value.get("volume_dbfs")
    if (type(pitch) in (int, float) and 60 <= pitch <= 500
            and type(volume) in (int, float) and MIN_CALIBRATION_DBFS <= volume <= 0):
        return pitch, volume
    return None


@dataclass
class AlertRule:
    duration_seconds: float
    cooldown_seconds: float = 3.0
    active_blocks: int = 0
    last_alert_seconds: float = -float("inf")
    alerted: bool = False
    grace_blocks: int = 0
    missing_blocks: int = 0
    new_incident: bool = False
    span_blocks: int = 0

    @property
    def minimum_blocks(self):
        return max(1, round(self.duration_seconds / BLOCK_SECONDS))

    def update(self, active, elapsed_seconds):
        self.new_incident = False
        if active is None:
            self.missing_blocks += 1
            if self.missing_blocks > self.grace_blocks:
                self.reset()
            elif self.active_blocks:
                self.span_blocks += 1
            return False
        if not active:
            self.reset()
            return False

        self.missing_blocks = 0
        self.active_blocks += 1
        self.span_blocks += 1
        self.new_incident = self.active_blocks == self.minimum_blocks
        if (not self.alerted
                and self.active_blocks >= self.minimum_blocks
                and elapsed_seconds - self.last_alert_seconds >= self.cooldown_seconds):
            self.alerted = True
            self.last_alert_seconds = elapsed_seconds
            return True
        return False

    def reset(self):
        self.active_blocks = 0
        self.span_blocks = 0
        self.missing_blocks = 0
        self.new_incident = False
        self.alerted = False


class VoiceMonitor:
    # short banner text for each alert (the full sentences are in the docstrings below)
    QUIET, VOLUME_SPIKE, PITCH_SPIKE = "Speak up", "Steady your volume", "Steady your pitch"
    PAUSE = "Keep talking"

    def __init__(self, quiet_drop_db=8.0, noise_floor_dbfs=DEFAULT_NOISE_FLOOR_DBFS,
                 quiet_seconds=0.7, volume_jump_db=10.0, pitch_jump_semitones=6.0, pause_seconds=5.0,
                 baseline=None, recording_baseline=False):
        self.quiet_drop_db = quiet_drop_db
        self.noise_floor_dbfs = noise_floor_dbfs
        self.volume_jump_db = volume_jump_db
        self.pitch_jump_semitones = pitch_jump_semitones
        self.latest_pitch_hz = None
        self.latest_volume_dbfs = None
        self.typical_pitch_hz, self.typical_volume_dbfs = baseline or (None, None)
        self.baseline_source = "calibration" if baseline else ("recording" if recording_baseline else None)
        self.recording_baseline = recording_baseline and baseline is None
        self.recording_measurements = []
        if self.recording_baseline:
            # A recording's gain may be much quieter than the live microphone. Its consistency
            # reference is calculated after the complete file has been measured.
            self.noise_floor_dbfs = QUIET_PITCH_FLOOR_DBFS
        if self.typical_volume_dbfs is not None:
            self.noise_floor_dbfs = min(
                self.noise_floor_dbfs, max(MIN_CALIBRATION_DBFS, self.typical_volume_dbfs - 6)
            )
        # "not speaking" level from the room's measured noise (set_silence); None = unknown, then the
        # fixed noise_floor_dbfs gates pitch and volume measurements
        self.silence_dbfs = None
        self.speaking = False
        self.quiet = AlertRule(quiet_seconds, grace_blocks=QUIET_GAP_BLOCKS)
        self.volume_spike = AlertRule(0.5)          # 10 dB above typical for 0.5 s (was 6 dB, 0.3 s)
        self.pitch_spike = AlertRule(0.3, grace_blocks=2)
        self.pitch_direction = 0
        self.stable_pitch_hz = None
        self.pitch_gap_blocks = 0
        self.harmonic_candidate_hz = None
        self.harmonic_blocks = 0
        # long pause: not speaking (no voiced audio above the silence level) for pause_seconds, once
        # the speaker has started talking (so the time before the first word doesn't count)
        self.pause = AlertRule(pause_seconds)
        self.voiced_run = 0
        self.has_spoken = False

    @property
    def quiet_limit_dbfs(self):
        if self.typical_volume_dbfs is None:
            return None
        return self.typical_volume_dbfs - self.quiet_drop_db

    def _confirm_pitch(self, pitch):
        if pitch is None:
            self.pitch_gap_blocks += 1
            if self.pitch_gap_blocks >= HARMONIC_CONFIRM_BLOCKS:
                self.stable_pitch_hz = None
            self.harmonic_blocks = 0
            return None

        self.pitch_gap_blocks = 0
        reference = self.stable_pitch_hz or self.typical_pitch_hz
        if reference is not None:
            ratio = pitch / reference
            harmonic = round(ratio)
            if ratio >= 2.5 or (harmonic == 2 and abs(ratio / harmonic - 1) <= 0.08):
                # Confirm abrupt octave-like jumps before showing or grading them.
                if self.harmonic_blocks and abs(pitch / self.harmonic_candidate_hz - 1) <= 0.08:
                    self.harmonic_blocks += 1
                else:
                    self.harmonic_blocks = 1
                self.harmonic_candidate_hz = pitch
                if self.harmonic_blocks < HARMONIC_CONFIRM_BLOCKS:
                    return None
        self.harmonic_blocks = 0
        self.stable_pitch_hz = pitch
        return pitch

    def measure(self, samples):
        """Measure pitch and volume for one 50 ms mono audio block."""
        volume = volume_dbfs(samples)
        gate = self.noise_floor_dbfs if self.silence_dbfs is None else self.silence_dbfs
        # A high room gate must not discard quiet speech that still has a detectable pitch.
        pitch_gate = min(gate, QUIET_PITCH_FLOOR_DBFS) if self.quiet_limit_dbfs is not None else gate
        measured_pitch = pitch_hz(samples) if volume >= pitch_gate else None
        pitch = self._confirm_pitch(measured_pitch)
        self.speaking = volume >= gate or pitch is not None
        self.latest_pitch_hz = pitch
        self.latest_volume_dbfs = volume
        return pitch, volume

    def _evaluate_measurement(self, pitch, volume, speaking, elapsed_seconds):
        """Apply the consistency rules to one already-measured 50 ms block."""
        self.speaking = speaking
        typical_volume = self.typical_volume_dbfs
        typical_pitch = self.typical_pitch_hz
        quiet_limit = self.quiet_limit_dbfs
        too_quiet = False
        if quiet_limit is not None and volume <= quiet_limit:
            # A quiet syllable above the room cutoff can count even without a stable pitch.
            # Short pauses keep the incident open without adding to its duration.
            too_quiet = True if self.speaking else None
        loud = (self.speaking and typical_volume is not None
                and volume - typical_volume >= self.volume_jump_db)
        pitch_swing = None if pitch is None else False
        if pitch is not None and typical_pitch is not None:
            change = 12 * np.log2(pitch / typical_pitch)
            pitch_swing = abs(change) >= self.pitch_jump_semitones
            if pitch_swing:
                direction = 1 if change > 0 else -1
                if direction != self.pitch_direction:
                    self.pitch_spike.reset()
                    self.pitch_direction = direction

        self.voiced_run = self.voiced_run + 1 if pitch is not None else 0
        talking = self.voiced_run >= SPEECH_RESUME_BLOCKS
        self.has_spoken = self.has_spoken or talking

        alerts = []
        if self.pause.update(self.has_spoken and not talking, elapsed_seconds):
            alerts.append(self.PAUSE)
        if self.quiet.update(too_quiet, elapsed_seconds):
            alerts.append(self.QUIET)
        if self.volume_spike.update(loud, elapsed_seconds):
            alerts.append(self.VOLUME_SPIKE)
        if self.pitch_spike.update(pitch_swing, elapsed_seconds):
            alerts.append(self.PITCH_SPIKE)
        return alerts

    def process(self, samples, elapsed_seconds):
        """Return live alerts for one 50 ms mono audio block."""
        pitch, volume = self.measure(samples)
        if self.recording_baseline:
            # Uploads are graded after the whole file establishes a robust reference. Do not let
            # an unusually soft or emphatic introduction decide what "normal" means.
            self.recording_measurements.append((pitch, volume, self.speaking))
            return []
        return self._evaluate_measurement(pitch, volume, self.speaking, elapsed_seconds)

    def finalize_recording_baseline(self):
        """Score an upload against the median pitch and volume of all voiced audio.

        Returns ``None`` when there is not enough voiced material to measure tone, otherwise a
        list of ``(block_index, rule_id)`` sustained consistency incidents.
        """
        if not self.recording_baseline:
            return None
        voiced = [(pitch, volume) for pitch, volume, _ in self.recording_measurements if pitch is not None]
        if len(voiced) < RECORDING_MIN_VOICED_BLOCKS:
            return None
        self.typical_pitch_hz = float(median(pitch for pitch, _ in voiced))
        self.typical_volume_dbfs = float(median(volume for _, volume in voiced))

        # Replay the measured recording through a monitor with the final reference. This preserves
        # the same duration, gap and direction rules used for live calibration-based grading.
        reference = VoiceMonitor(
            quiet_drop_db=self.quiet_drop_db,
            volume_jump_db=self.volume_jump_db,
            pitch_jump_semitones=self.pitch_jump_semitones,
            pause_seconds=self.pause.duration_seconds,
            baseline=(self.typical_pitch_hz, self.typical_volume_dbfs),
        )
        reference.quiet.duration_seconds = self.quiet.duration_seconds
        reference.volume_spike.duration_seconds = self.volume_spike.duration_seconds
        reference.pitch_spike.duration_seconds = self.pitch_spike.duration_seconds
        incidents = []
        detectors = (("quiet", "quiet"), ("volume", "volume_spike"), ("pitch", "pitch_spike"))
        for block, (pitch, volume, speaking) in enumerate(self.recording_measurements):
            reference._evaluate_measurement(pitch, volume, speaking, (block + 1) * BLOCK_SECONDS)
            for rule, attribute in detectors:
                if getattr(reference, attribute).new_incident:
                    incidents.append((block, rule))
        return incidents

    def reset_pending_alerts(self):
        self.quiet.reset()
        self.pause.reset()
        self.volume_spike.reset()
        self.pitch_spike.reset()
