"""Voice pitch spikes, volume spikes and sustained low volume, on 50 ms blocks of 16 kHz audio.
VoiceMonitor is from experiments/gap_heuristic/voice_pitch.py; the calibration flow (collect a few
seconds of voiced audio as the speaker's normal level) is from voice_pitch_gui.py."""

from collections import deque
from dataclasses import dataclass
from statistics import median

import numpy as np


SAMPLE_RATE = 16_000
BLOCK_SECONDS = 0.05
BLOCK_SIZE = round(SAMPLE_RATE * BLOCK_SECONDS)
BASELINE_MIN_BLOCKS = round(1 / BLOCK_SECONDS)
BASELINE_HISTORY_BLOCKS = round(4 / BLOCK_SECONDS)
PITCH_FRAME_SIZE = round(SAMPLE_RATE * 0.04)
MIN_PITCH_LAG = SAMPLE_RATE // 450
MAX_PITCH_LAG = SAMPLE_RATE // 70
CALIBRATION_VOICED_SECONDS = 3
CALIBRATION_TIMEOUT_SECONDS = 10


def pitch_hz(samples):
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
    if not peaks.size or scores[peaks].max() < 0.75:
        return None

    # Prefer the shortest plausible period to avoid reporting a subharmonic.
    cutoff = max(0.75, 0.9 * scores[peaks].max())
    best = peaks[scores[peaks] >= cutoff][0]
    return SAMPLE_RATE / lags[best]


@dataclass
class AlertRule:
    duration_seconds: float
    cooldown_seconds: float = 3.0
    active_blocks: int = 0
    last_alert_seconds: float = -float("inf")
    alerted: bool = False

    def update(self, active, elapsed_seconds):
        if not active:
            self.reset()
            return False

        self.active_blocks += 1
        if (not self.alerted
                and self.active_blocks >= round(self.duration_seconds / BLOCK_SECONDS)
                and elapsed_seconds - self.last_alert_seconds >= self.cooldown_seconds):
            self.alerted = True
            self.last_alert_seconds = elapsed_seconds
            return True
        return False

    def reset(self):
        self.active_blocks = 0
        self.alerted = False


class VoiceMonitor:
    # short banner text for each alert (the full sentences are in the docstrings below)
    QUIET, VOLUME_SPIKE, PITCH_SPIKE = "Speak up", "Steady your volume", "Steady your pitch"

    def __init__(self, quiet_dbfs=-45.0, quiet_drop_db=15.0, noise_floor_dbfs=-60.0,
                 quiet_seconds=3.0, volume_jump_db=8.0, pitch_jump_semitones=9.0):
        self.quiet_dbfs = quiet_dbfs
        self.quiet_drop_db = quiet_drop_db
        self.noise_floor_dbfs = noise_floor_dbfs
        self.volume_jump_db = volume_jump_db
        self.pitch_jump_semitones = pitch_jump_semitones
        self.volume_history = deque(maxlen=BASELINE_HISTORY_BLOCKS)
        self.pitch_history = deque(maxlen=BASELINE_HISTORY_BLOCKS)
        self.latest_pitch_hz = None
        self.latest_volume_dbfs = None
        self.typical_pitch_hz = None          # calibrated pitch, or the running median before that
        self.calibrated_baseline = None
        self.quiet = AlertRule(quiet_seconds)
        self.volume_spike = AlertRule(0.3)
        self.pitch_spike = AlertRule(0.2)

    def measure(self, samples):
        """Measure pitch and volume for one 50 ms mono audio block."""
        rms = np.sqrt(np.mean(np.square(samples, dtype=np.float64)))
        volume = 20 * np.log10(max(rms, 1e-8))
        pitch = pitch_hz(samples) if volume >= self.noise_floor_dbfs else None
        self.latest_pitch_hz = pitch
        self.latest_volume_dbfs = volume
        return pitch, volume

    def calibrate(self, voiced_readings):
        pitches, volumes = zip(*voiced_readings)
        self.calibrated_baseline = median(pitches), median(volumes)
        self.typical_pitch_hz = self.calibrated_baseline[0]
        self.reset_pending_alerts()
        return self.calibrated_baseline

    def process(self, samples, elapsed_seconds):
        """Return alerts for one 50 ms mono audio block: QUIET (too quiet or paused for too long),
        VOLUME_SPIKE (keep your level steady), PITCH_SPIKE (avoid sudden jumps in pitch)."""
        pitch, volume = self.measure(samples)
        # Use voiced audio to establish typical levels without including room noise.
        if self.calibrated_baseline is None:
            typical_volume = (median(self.volume_history)
                              if len(self.volume_history) >= BASELINE_MIN_BLOCKS else None)
            typical_pitch = (median(self.pitch_history)
                             if len(self.pitch_history) >= BASELINE_MIN_BLOCKS else None)
        else:
            typical_pitch, typical_volume = self.calibrated_baseline
        self.typical_pitch_hz = typical_pitch
        # Before calibration the fixed level is a floor; after it, quiet is relative to the speaker.
        if self.calibrated_baseline is not None:
            quiet_threshold = max(self.noise_floor_dbfs, typical_volume - self.quiet_drop_db)
        elif typical_volume is not None:
            quiet_threshold = max(self.quiet_dbfs, typical_volume - self.quiet_drop_db)
        else:
            quiet_threshold = self.quiet_dbfs

        too_quiet = volume < quiet_threshold
        loud = typical_volume is not None and volume - typical_volume >= self.volume_jump_db
        high_pitch = (not too_quiet and pitch is not None and typical_pitch is not None
                      and 12 * np.log2(pitch / typical_pitch) >= self.pitch_jump_semitones)

        if self.calibrated_baseline is None and pitch is not None and not too_quiet:
            if not loud:
                self.volume_history.append(volume)
            if not high_pitch:
                self.pitch_history.append(pitch)

        alerts = []
        if self.quiet.update(too_quiet, elapsed_seconds):
            alerts.append(self.QUIET)
        if self.volume_spike.update(loud, elapsed_seconds):
            alerts.append(self.VOLUME_SPIKE)
        if self.pitch_spike.update(high_pitch, elapsed_seconds):
            alerts.append(self.PITCH_SPIKE)
        return alerts

    def reset_pending_alerts(self):
        self.quiet.reset()
        self.volume_spike.reset()
        self.pitch_spike.reset()


class VoiceCalibration:
    """Collects CALIBRATION_VOICED_SECONDS of voiced audio (giving up after the timeout) and sets
    it as the speaker's normal pitch and volume."""

    def __init__(self, monitor):
        self.monitor = monitor
        self.readings = []
        self.started_block = None
        self.message = ""

    @property
    def active(self):
        return self.started_block is not None

    def start(self, block):
        self.readings.clear()
        self.started_block = block
        self.message = "Voice: speak a few sentences in your normal voice"

    def collect(self, samples, block):
        """Feed one block while active; returns True when calibration just finished."""
        pitch, volume = self.monitor.measure(samples)
        if pitch is not None:
            self.readings.append((pitch, volume))
        voiced = len(self.readings) * BLOCK_SECONDS
        elapsed = (block - self.started_block) * BLOCK_SECONDS
        if voiced >= CALIBRATION_VOICED_SECONDS:
            pitch, volume = self.monitor.calibrate(self.readings)
            self.message = f"Voice calibrated: {pitch:.0f} Hz, {volume:.0f} dBFS"
        elif elapsed >= CALIBRATION_TIMEOUT_SECONDS:
            self.message = "Voice: too little speech to calibrate - try again"
        else:
            self.message = f"Voice: keep talking normally ({voiced:.1f} / {CALIBRATION_VOICED_SECONDS} s)"
            return False
        self.started_block = None
        self.readings.clear()
        self.monitor.reset_pending_alerts()
        return True
