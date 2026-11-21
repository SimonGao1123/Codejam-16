"""Voice pitch spikes, volume spikes and sustained low volume, on 50 ms blocks of 16 kHz audio.
VoiceMonitor is from experiments/gap_heuristic/voice_pitch.py.

There's no voice calibration step: the speaker's typical pitch and volume are always the median of
their last BASELINE_SPEECH_S seconds of speech. Silence doesn't count (only blocks above the room's
silence level, measured at session start, have a pitch), so a pause doesn't empty the window."""

from collections import deque
from dataclasses import dataclass
from statistics import median

import numpy as np


SAMPLE_RATE = 16_000
BLOCK_SECONDS = 0.05
BLOCK_SIZE = round(SAMPLE_RATE * BLOCK_SECONDS)
BASELINE_SPEECH_S = 10            # typical pitch / volume = median of the last 10 s of speech
BASELINE_MIN_BLOCKS = round(1 / BLOCK_SECONDS)                   # ...once there's 1 s of speech
BASELINE_HISTORY_BLOCKS = round(BASELINE_SPEECH_S / BLOCK_SECONDS)
# a pause ends only after this much continuous voiced audio, so a click, cough or noise blip just
# above the silence level doesn't reset the long-pause timer
SPEECH_RESUME_BLOCKS = 3          # 150 ms
PITCH_FRAME_SIZE = round(SAMPLE_RATE * 0.04)
MIN_PITCH_LAG = SAMPLE_RATE // 450
MAX_PITCH_LAG = SAMPLE_RATE // 70


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
    PAUSE = "Keep talking"

    def __init__(self, quiet_dbfs=-45.0, quiet_drop_db=15.0, noise_floor_dbfs=-60.0,
                 quiet_seconds=3.0, volume_jump_db=8.0, pitch_jump_semitones=9.0, pause_seconds=5.0):
        self.quiet_dbfs = quiet_dbfs
        self.quiet_drop_db = quiet_drop_db
        self.noise_floor_dbfs = noise_floor_dbfs
        self.volume_jump_db = volume_jump_db
        self.pitch_jump_semitones = pitch_jump_semitones
        self.volume_history = deque(maxlen=BASELINE_HISTORY_BLOCKS)
        self.pitch_history = deque(maxlen=BASELINE_HISTORY_BLOCKS)
        self.latest_pitch_hz = None
        self.latest_volume_dbfs = None
        self.typical_pitch_hz = None          # median pitch of the last BASELINE_SPEECH_S of speech
        self.typical_volume_dbfs = None
        # "not speaking" level from the room's measured noise (set_silence); None = unknown, then the
        # fixed noise_floor_dbfs gates pitch and every quiet block counts as "too quiet"
        self.silence_dbfs = None
        self.speaking = False
        self.quiet = AlertRule(quiet_seconds)
        self.volume_spike = AlertRule(0.3)
        self.pitch_spike = AlertRule(0.2)
        # long pause: not speaking (no voiced audio above the silence level) for pause_seconds, once
        # the speaker has started talking (so the time before the first word doesn't count)
        self.pause = AlertRule(pause_seconds)
        self.voiced_run = 0
        self.has_spoken = False

    def measure(self, samples):
        """Measure pitch and volume for one 50 ms mono audio block."""
        rms = np.sqrt(np.mean(np.square(samples, dtype=np.float64)))
        volume = 20 * np.log10(max(rms, 1e-8))
        gate = self.noise_floor_dbfs if self.silence_dbfs is None else self.silence_dbfs
        self.speaking = volume >= gate
        pitch = pitch_hz(samples) if self.speaking else None
        self.latest_pitch_hz = pitch
        self.latest_volume_dbfs = volume
        return pitch, volume

    def process(self, samples, elapsed_seconds):
        """Return alerts for one 50 ms mono audio block: QUIET (speaking too quietly), PAUSE (not
        speaking for too long), VOLUME_SPIKE (keep your level steady), PITCH_SPIKE (avoid sudden
        jumps in pitch)."""
        pitch, volume = self.measure(samples)
        # typical levels = median of the last BASELINE_SPEECH_S of speech (room noise excluded)
        typical_volume = (median(self.volume_history)
                          if len(self.volume_history) >= BASELINE_MIN_BLOCKS else None)
        typical_pitch = (median(self.pitch_history)
                         if len(self.pitch_history) >= BASELINE_MIN_BLOCKS else None)
        self.typical_pitch_hz = typical_pitch
        self.typical_volume_dbfs = typical_volume
        # quiet = well below the speaker's own typical level, with a fixed floor
        quiet_threshold = (max(self.quiet_dbfs, typical_volume - self.quiet_drop_db)
                           if typical_volume is not None else self.quiet_dbfs)

        too_quiet = volume < quiet_threshold
        if self.silence_dbfs is not None:
            # with the room's silence level known, "too quiet" means speaking quietly; silence
            # (at the room's noise level) is a pause, not quiet speech
            too_quiet = too_quiet and self.speaking
        loud = typical_volume is not None and volume - typical_volume >= self.volume_jump_db
        high_pitch = (not too_quiet and pitch is not None and typical_pitch is not None
                      and 12 * np.log2(pitch / typical_pitch) >= self.pitch_jump_semitones)

        if pitch is not None and not too_quiet:
            if not loud:
                self.volume_history.append(volume)
            if not high_pitch:
                self.pitch_history.append(pitch)

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
        if self.pitch_spike.update(high_pitch, elapsed_seconds):
            alerts.append(self.PITCH_SPIKE)
        return alerts

    def reset_pending_alerts(self):
        self.quiet.reset()
        self.pause.reset()
        self.volume_spike.reset()
        self.pitch_spike.reset()
