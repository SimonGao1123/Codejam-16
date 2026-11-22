"""
Webcam analysis: posture, eye level and hand motion from MediaPipe landmarks sent by the
Electron app. Ported from experiments/mediapipe_webcam/webcam_test.py (same measurements and
thresholds), minus OpenCV/MediaPipe: Electron runs the models and draws the overlay this returns.

  - shoulder tilt (degrees)          -> "Level your shoulders"  (vs calibration)
  - eye level: head pitch + eye gaze -> "Look up"               (vs calibration)
  - face and shoulders in frame     -> "Stay in frame"
  - both hands visible               -> "Show both hands or step back"
  - hand motion: none for ~10 s      -> "Use your hands"
                 too fast / too busy -> "Slow your hands"
                 same gesture looping-> "Vary your gestures"  (wrists and fingers)
  - hand angle (per hand)            -> label next to each wrist, no alert

All timing uses the frame's `t` (seconds, Electron's monotonic clock), so a recorded .jsonl
session replays identically. Message format: docs/webcam-protocol.md.
"""

import math
import statistics
from collections import deque
from types import SimpleNamespace

import numpy as np

L_SHOULDER, R_SHOULDER, L_WRIST, R_WRIST = 11, 12, 15, 16      # pose landmarks
HAND_WRIST, HAND_MIDDLE_MCP = 0, 9                             # hand landmarks
FOREHEAD, CHIN = 10, 152                                       # face landmarks
FACE_EDGES = (FOREHEAD, CHIN, 234, 454)
FRAMING_MARGIN = 0.03
MIN_FACE_HEIGHT = 0.08

# Thresholds (guesses - tune on real recordings). Tilt and eye level are deviations from calibration.
TILT_MAX_DEG = 6            # shoulder tilt this far from calibrated = uneven
LOOK_DOWN_MAX_DEG = 15      # head pitched down this far past calibrated = looking down
EYES_DOWN_MAX = 0.35        # eyeLookDown blendshape score (0-1) this far above calibrated = eyes down
# Hand thresholds are absolute (not calibrated). Wrist positions are in shoulder widths from the
# shoulder midpoint, so distance to the camera and body sway don't matter.
FACE_MISSING_S = 1.0        # poor framing for this long = warn
HANDS_MISSING_S = 1.0       # fewer than 2 hands seen for this long = warn
HAND_STILL = 0.15           # average wrist speed (shoulder-widths/s) over the last 1 s below this = not moving
HAND_STILL_S = 10           # not moving (or both hands at rest) for this long = warn
REST_RADIUS = 0.3           # wrist within this many shoulder widths of its calibrated rest spot = at rest
HAND_GRACE_S = 0.5          # a hand dropping out for less than this still counts as "both hands visible"
HAND_FAST = 3.0             # wrist speed over the last 0.3 s above this = too fast
HAND_BUSY = 1.5             # average wrist speed over the last 5 s above this = too much motion
REPEAT_WINDOW_S = 6.0       # repetition is checked over this much wrist history
REPEAT_SCORE = 0.8          # self-similarity peak (0-1) above this = same gesture looping (tests: repeats 0.95+, non-repeats <= 0.6)
REPEAT_MIN_AMP = 0.08       # ignore tiny wrist motions (shoulder widths, after removing drift)
FINGER_MIN_AMP = 0.25       # ignore tiny finger motions (hand lengths, all finger points combined)
REPEAT_MIN_COVERAGE = 0.6   # a wrist/hand must be seen in this share of the window to be checked
REPEAT_LAGS_S = (0.4, 2.5)  # a repeated gesture takes between this many seconds per cycle
REPEAT_DIP = 0.3            # similarity must first drop below this before a later peak counts as a cycle
REPEAT_DETREND_S = 3.0      # moving average subtracted before the check; >= longest cycle so cycles survive
# Finger points tracked relative to the wrist, in hand lengths (wrist -> middle knuckle):
# thumb, index, middle and pinky tips + middle knuckle (hand orientation / wrist bend).
FINGER_POINTS = (4, 8, 12, 20, 9)
CALIBRATE_DELAY_S = 2.0     # default pause before recording (the app's session calibration uses 0)
CALIBRATE_S = 2.0           # default recording length (the app's session calibration uses 3 s)
# "when ready" calibration (start of each session): recording only starts once the face, both
# shoulders are in frame and the speaker holds fairly still for READY_HOLD_S. The hands visibility
# setting also controls whether both hands are required.
READY_HOLD_S = 0.75
READY_STILL = 0.3           # wrist movement (shoulder widths/s, last 1 s) below this = holding still
READY_BODY_STILL = 0.15     # shoulder (torso) movement (shoulder widths/s, last 1 s) below this
READY_ABORT_S = 0.4         # not ready for this long while recording = start over


class Alert:
    """Fires once a condition has held for `hold_s`, stays on screen `show_s` after it clears, then
    can't fire again for `cooldown_s`. All three (and `enabled`) come from the Live alerts settings."""

    def __init__(self, text, hold_s, show_s=1.5, cooldown_s=0.0):
        self.text, self.hold_s, self.show_s, self.cooldown_s = text, hold_s, show_s, cooldown_s
        self.enabled = True
        self.reset()

    def update(self, bad, now):
        if not self.enabled:
            self.reset()
            return False
        if not bad:
            self.bad_since = None
        elif self.bad_since is None:
            self.bad_since = now
        elif now - self.bad_since >= self.hold_s and (self.showing or now >= self.ready_at):
            self.visible_until = now + self.show_s
        was_showing, self.showing = self.showing, now < self.visible_until
        if was_showing and not self.showing:
            self.ready_at = now + self.cooldown_s
        return self.showing

    def configure(self, enabled, duration, cooldown):
        self.enabled, self.hold_s, self.cooldown_s = enabled, duration, cooldown

    def reset(self):
        self.bad_since, self.visible_until, self.showing, self.ready_at = None, 0.0, False, 0.0


def points(raw):
    """[[x, y, z(, visibility)], ...] -> objects with .x .y .z .visibility, like MediaPipe's."""
    return [SimpleNamespace(x=p[0], y=p[1], z=p[2], visibility=p[3] if len(p) > 3 else 1.0) for p in raw]


def shoulder_tilt(lm, w, h):
    """Shoulder tilt in degrees (0 = level) and shoulder width in pixels."""
    ls, rs = lm[L_SHOULDER], lm[R_SHOULDER]
    dx, dy = (rs.x - ls.x) * w, (rs.y - ls.y) * h
    tilt = math.degrees(math.atan2(dy, dx))
    tilt = tilt - 180 if tilt > 90 else tilt + 180 if tilt < -90 else tilt
    return tilt, math.hypot(dx, dy) or 1


def inside_frame(x, y):
    return (FRAMING_MARGIN <= x <= 1 - FRAMING_MARGIN
            and FRAMING_MARGIN <= y <= 1 - FRAMING_MARGIN)


def face_in_frame(face):
    if not face:
        return False
    landmarks = face["landmarks"]
    return (landmarks[CHIN][1] - landmarks[FOREHEAD][1] >= MIN_FACE_HEIGHT
            and all(inside_frame(*landmarks[i][:2]) for i in FACE_EDGES))


def shoulders_in_frame(pose):
    return bool(pose and all(
        pose[i].visibility > 0.5 and inside_frame(pose[i].x, pose[i].y)
        for i in (L_SHOULDER, R_SHOULDER)
    ))


def head_pitch(face, w, h):
    """Head pitch in degrees from the forehead->chin line; grows as the head tips down.
    Face landmark z is in roughly the same units as x (smaller = closer to camera)."""
    top, chin = face[FOREHEAD], face[CHIN]
    return math.degrees(math.atan2((chin.z - top.z) * w, (chin.y - top.y) * h))


def eyes_down(blendshapes):
    """blendshapes: {name: score}."""
    return (blendshapes.get("eyeLookDownLeft", 0) + blendshapes.get("eyeLookDownRight", 0)) / 2


def hand_angle(hand, w, h):
    """Angle of the wrist -> middle knuckle line: 0 = fingers up, +90 = right, -90 = left."""
    a, b = hand[HAND_WRIST], hand[HAND_MIDDLE_MCP]
    return math.degrees(math.atan2((b.x - a.x) * w, -(b.y - a.y) * h))


class HandMotion:
    """Keeps a few seconds of wrist and finger positions and measures speed, busyness and repetition."""

    RATE = 15                       # Hz; history is resampled to this for the repetition check
    CHANNELS = ("L wrist", "R wrist", "L fingers", "R fingers")

    def __init__(self):
        self.history = deque()      # (time, [L wrist, R wrist, L fingers, R fingers]); each array or None
        self.speeds = deque()       # (time, speed over the last 0.3 s)

    def clear(self):
        self.history.clear()
        self.speeds.clear()

    def add(self, now, wrists, fingers):
        """wrists: (x, y) per side in shoulder widths; fingers: flat array per side in hand lengths."""
        self.history.append((now, [w and np.array(w) for w in wrists] + list(fingers)))
        while now - self.history[0][0] > REPEAT_WINDOW_S:
            self.history.popleft()
        self.speeds.append((now, self.speed(0.3)))
        while now - self.speeds[0][0] > 5.0:
            self.speeds.popleft()

    def speed(self, window):
        """Fastest wrist's speed over the last `window` seconds, in shoulder-widths per second."""
        if not self.history:
            return 0.0
        recent = [e for e in self.history if self.history[-1][0] - e[0] <= window]
        best = 0.0
        for i in range(2):
            seen = [(t, c[i]) for t, c in recent if c[i] is not None]
            if len(seen) >= 2:
                (t0, a), (t1, b) = seen[0], seen[-1]
                best = max(best, float(np.linalg.norm(a - b)) / max(t1 - t0, 1e-6))
        return best

    def activity(self, window):
        """Average of the 0.3 s speeds over the last `window` seconds (max 5 s). Unlike speed(window),
        a gesture that ends where it started still counts as motion."""
        recent = [v for t, v in self.speeds if self.speeds[-1][0] - t <= window]
        return sum(recent) / len(recent) if recent else 0.0

    def repetition(self, wrists=True):
        """(score 0-1, channel name): the strongest self-repeating wrist or finger motion.
        wrists=False checks fingers only (resting wrist positions just wobble)."""
        if not self.history or self.history[-1][0] - self.history[0][0] < 0.8 * REPEAT_WINDOW_S:
            return 0.0, None
        times = np.array([t for t, _ in self.history])
        grid = np.arange(times[0], times[-1], 1 / self.RATE)
        best, where = 0.0, None
        for i, name in enumerate(self.CHANNELS):
            if i < 2 and not wrists:
                continue
            score = self._channel_score(times, grid, [c[i] for _, c in self.history],
                                        REPEAT_MIN_AMP if i < 2 else FINGER_MIN_AMP)
            if score > best:
                best, where = score, name
        return best, where

    def _channel_score(self, times, grid, values, min_amp):
        mask = np.array([v is not None for v in values])
        if mask.mean() < REPEAT_MIN_COVERAGE:
            return 0.0
        pts = np.array([v for v in values if v is not None])
        sig = np.column_stack([np.interp(grid, times[mask], pts[:, j]) for j in range(pts.shape[1])])
        # subtract a moving average so drift and sudden repositioning (arm sinking, moving the hand
        # somewhere new) aren't mistaken for, or hide, a cycle
        k = int(REPEAT_DETREND_S * self.RATE) | 1
        padded = np.pad(sig, ((k // 2, k // 2), (0, 0)), mode="edge")
        kernel = np.ones(k) / k
        sig = sig - np.column_stack([np.convolve(padded[:, j], kernel, "valid") for j in range(sig.shape[1])])
        if math.sqrt((sig * sig).sum(axis=1).mean()) < min_amp:
            return 0.0
        n = len(sig)
        lo, hi = (int(x * self.RATE) for x in REPEAT_LAGS_S)
        # similarity of the path with itself shifted by k samples, normalized by both overlapping parts
        # so it stays within -1..1 (at least half the window always overlaps)
        ac = []
        for k in range(min(hi + 2, n // 2)):
            a, b = sig[:n - k], sig[k:]
            ac.append(float((a * b).sum() / (math.sqrt((a * a).sum() * (b * b).sum()) + 1e-12)))
        # a real repeat is a peak that comes after the similarity has dropped off
        best, dipped = 0.0, False
        for k in range(1, len(ac) - 1):
            dipped = dipped or ac[k] < REPEAT_DIP
            if dipped and lo <= k <= hi and ac[k] >= ac[k - 1] and ac[k] >= ac[k + 1]:
                best = max(best, ac[k])
        return best


def finger_features(hand, w, h):
    """Wrist pixel position and FINGER_POINTS relative to the wrist, in hand lengths (flat array)."""
    pts = np.array([(p.x * w, p.y * h) for p in hand])
    size = np.linalg.norm(pts[HAND_MIDDLE_MCP] - pts[HAND_WRIST]) or 1.0
    return pts[HAND_WRIST], ((pts[list(FINGER_POINTS)] - pts[HAND_WRIST]) / size).ravel()


def match_hands(hands, pose_wrists_px, w, h):
    """Assign each detected hand to the nearest pose wrist (index 0 = L_WRIST, 1 = R_WRIST)."""
    fingers = [None, None]
    best = [math.inf, math.inf]
    for hand in hands:
        wrist_px, feats = finger_features(hand, w, h)
        dists = [np.linalg.norm(wrist_px - p) if p is not None else math.inf for p in pose_wrists_px]
        side = int(np.argmin(dists))
        if dists[side] < best[side]:
            best[side], fingers[side] = dists[side], feats
    return fingers


class Calibration:
    """After start(), waits `delay` (CALIBRATE_DELAY_S by default), records each measurement for
    `duration` (CALIBRATE_S), then keeps the medians as the baseline. The app starts one with no
    delay and 3 s of recording at the start of every practice session."""

    KEYS = ("tilt", "pitch", "eyes")              # required
    OPTIONAL = ("rest_l", "rest_r")               # resting wrist (x, y); None if resting out of frame

    def __init__(self):
        self.baseline = None          # {"tilt": deg, "pitch": deg, "eyes": score, "rest_l"/"rest_r": (x, y) or None}
        self.begins = None            # recording starts at this time (after the get-into-position pause)
        self.until = None             # recording ends at this time
        self.armed = None             # recording length, while waiting for the speaker to be ready
        self.samples = {}
        self.include_hands = True
        self.message = "Calibrates when the session starts"

    def start(self, now, delay=CALIBRATE_DELAY_S, duration=CALIBRATE_S):
        self.armed = None
        self.begins = now + delay
        self.until = self.begins + duration
        self.samples = {k: [] for k in self.KEYS + self.OPTIONAL}

    def arm(self, duration, message="Waiting to calibrate: get in frame and hold still"):
        """Wait for the speaker to be in frame and still (WebcamAnalyzer checks), then record."""
        self.begins = self.until = None
        self.baseline = None          # a new session's calibration replaces the last one
        self.armed = duration
        self.message = message

    @property
    def recording(self):
        """True from start() until recording ends, including the pause before recording."""
        return self.until is not None

    @property
    def busy(self):
        """Armed or recording: alerts and scoring are paused."""
        return self.armed is not None or self.recording

    def waiting(self, now):
        """True during the get-into-position pause."""
        return self.recording and now < self.begins

    def add(self, now, **values):
        """Feed this frame's measurements (None = not seen). Returns True when recording just finished."""
        if not self.recording or self.waiting(now):
            return False
        if now < self.until:
            for k, v in values.items():
                if v is not None and (self.include_hands or k not in self.OPTIONAL):
                    self.samples[k].append(v)
            return False
        self.until = None
        missing = [k for k in self.KEYS if not self.samples[k]]
        if missing:
            self.message = f"Calibration failed: {', '.join(missing)} not seen - keep face and shoulders in frame"
            return True
        self.baseline = {k: statistics.median(self.samples[k]) for k in self.KEYS}
        for k in self.OPTIONAL:
            # wrist must be seen in at least half the frames, otherwise treat it as resting out of frame
            pts = self.samples[k]
            enough = len(pts) >= len(self.samples["tilt"]) / 2
            self.baseline[k] = tuple(statistics.median(c) for c in zip(*pts)) if enough else None
        rest = sum(self.baseline[k] is not None for k in self.OPTIONAL)
        self.message = "Calibrated: tilt {tilt:+.1f} deg, head pitch {pitch:+.1f} deg, eyes down {eyes:.2f}".format(
            **self.baseline)
        if self.include_hands:
            self.message += f", resting hands in frame: {rest}/2"
        return True

    def state(self, now):
        phase = ("arming" if self.armed is not None else "waiting" if self.waiting(now)
                 else "recording" if self.recording else "idle")
        hands_hint = ", hands at rest" if self.include_hands else ""
        if phase == "waiting":
            self.message = f"Get into position - stand normally{hands_hint}, look at the camera"
        elif phase == "recording":
            self.message = f"Calibrating - stand still in your resting position{hands_hint}, look at the camera"
        remaining = self.begins - now if phase == "waiting" else self.until - now if phase == "recording" else 0
        return {"phase": phase, "remaining": round(max(remaining, 0), 2),
                "calibrated": self.baseline is not None, "message": self.message}


def line(text, color="white"):
    return {"text": text, "color": color}


class WebcamAnalyzer:
    """Turns `frame` / `calibrate` messages into `state` messages (see docs/webcam-protocol.md)."""

    def __init__(self):
        self.tilt_alert = Alert("Level your shoulders", hold_s=1.5)
        self.down_alert = Alert("Look up", hold_s=1.0)
        self.face_alert = Alert("Stay in frame", hold_s=FACE_MISSING_S)
        self.missing_alert = Alert("Show both hands or step back", hold_s=HANDS_MISSING_S)
        self.still_alert = Alert("Use your hands", hold_s=HAND_STILL_S)
        self.fast_alert = Alert("Slow your hands", hold_s=0.4)
        self.repeat_alert = Alert("Vary your gestures", hold_s=1.0)
        self.calib = Calibration()
        self.calibration_request_id = None
        self.motion = HandMotion()
        self.both_seen_at = -math.inf       # last time both hands were detected
        self.last_t = 0.0
        self.ready_since = None             # in frame and still since (calibration arming)
        self.not_ready_since = None         # out of frame or moving since (while recording)
        self.restarted = False              # this calibration was interrupted and is waiting again
        self.torso = deque()                # (t, shoulder midpoint x, y in shoulder widths) for stillness
        # Live alerts rule id -> (alert, threshold setter); thresholds default to the constants above
        self.limits = {"tilt": TILT_MAX_DEG, "look_down": LOOK_DOWN_MAX_DEG, "fast": HAND_FAST,
                       "repeat": REPEAT_SCORE}
        self.rules = {"tilt": self.tilt_alert, "look_down": self.down_alert, "face": self.face_alert,
                      "hands_missing": self.missing_alert, "still": self.still_alert,
                      "fast": self.fast_alert, "repeat": self.repeat_alert}
        self.scoring = False
        self.scoring_started_at = None
        self.scoring_mode = None
        self.recording_face_samples = []
        self.incidents = {}
        self.checkpoints = []
        self._incident_since = {}
        self._counted_incidents = set()

    def start_scoring(self, started_at=None, mode=None):
        self.scoring = True
        self.scoring_started_at = started_at
        self.scoring_mode = mode
        # An uploaded recording has no interactive "stand still" step. Build an independent
        # three-second baseline from its opening frames instead of reusing a live session's
        # calibration (which may belong to another person or camera setup). Scoring is paused
        # while Calibration collects the baseline.
        if mode == "recording":
            self.calib = Calibration()
            self.calib.start(float(started_at) if isinstance(started_at, (int, float)) else self.last_t,
                             delay=0, duration=3)
        self.recording_face_samples = []
        self.motion.clear()
        self.both_seen_at = -math.inf
        self.incidents.clear()
        self.checkpoints.clear()
        self._incident_since.clear()
        self._counted_incidents.clear()

    def _score_frame(self, now, conditions):
        """Count sustained conditions once, independently of banner visibility."""
        for rule, bad in conditions.items():
            if bad is not None:
                self.incidents.setdefault(rule, 0)
            if not bad:
                self._incident_since.pop(rule, None)
                self._counted_incidents.discard(rule)
                continue
            since = self._incident_since.setdefault(rule, now)
            if rule not in self._counted_incidents and now - since >= self.rules[rule].hold_s:
                self.incidents[rule] += 1
                self._counted_incidents.add(rule)
                self.checkpoints.append((since, "body_language", self.rules[rule].text))

    def apply_settings(self, rules):
        """Live alerts settings: {rule id: {enabled, threshold, duration, cooldown}}."""
        for rule_id, alert in self.rules.items():
            s = rules.get(rule_id)
            if not s:
                continue
            alert.configure(bool(s["enabled"]), float(s["duration"]), float(s["cooldown"]))
            if rule_id in self.limits and s.get("threshold") is not None:
                # the repeat slider is a percentage; the score is 0-1
                self.limits[rule_id] = s["threshold"] / 100 if rule_id == "repeat" else float(s["threshold"])

    def calibrate(self, t=None, delay=None, duration=None, when_ready=False, request_id=None):
        """Start calibration. Timed by the frames that follow; `t` defaults to the last frame's time.
        delay / duration: get-into-position pause and recording length (None = the defaults).
        when_ready: wait until the speaker is in frame and still. Hands are required only when
        the hands visibility setting is on at the start of calibration."""
        if request_id is not None and self.calibration_request_id == request_id:
            return
        if self.calib.busy and not when_ready:
            return
        self.calibration_request_id = request_id
        self.calib.include_hands = self.missing_alert.enabled
        duration = CALIBRATE_S if duration is None else duration
        if when_ready:
            # A new session can replace calibration left running by a cancelled session.
            self.calib.arm(duration)
            self.ready_since = self.not_ready_since = None
            self.restarted = False
            self.torso.clear()
        else:
            self.ready_since = None                 # timed calibration: never restarted
            self.calib.start(self.last_t if t is None else t,
                             CALIBRATE_DELAY_S if delay is None else delay, duration)

    def _readiness(self, now, pose, face_visible, shoulders_visible, both_recent, w, h):
        """What's stopping calibration right now: [] when the speaker is in frame and still."""
        missing = []
        if not face_visible:
            missing.append("show your face fully in frame")
        if not shoulders_visible:
            missing.append("step back so your shoulders are in frame")
        if self.calib.include_hands and not both_recent:
            missing.append("show both hands")
        if not missing:
            # torso: shoulder midpoint movement over the last second, in shoulder widths per second
            _, width = shoulder_tilt(pose, w, h)
            mx = (pose[L_SHOULDER].x + pose[R_SHOULDER].x) / 2 * w / width
            my = (pose[L_SHOULDER].y + pose[R_SHOULDER].y) / 2 * h / width
            self.torso.append((now, mx, my))
            while now - self.torso[0][0] > 1.0:
                self.torso.popleft()
            t0, x0, y0 = self.torso[0]
            body = math.hypot(mx - x0, my - y0) / max(now - t0, 0.25)
            hands_moving = self.calib.include_hands and self.motion.activity(1.0) > READY_STILL
            if hands_moving or body > READY_BODY_STILL:
                missing.append("hold still")
        else:
            self.torso.clear()
        return missing

    def _update_calibration(self, now, missing):
        """Arming: start recording once ready for READY_HOLD_S. Recording: start over if the speaker
        leaves the frame or moves for READY_ABORT_S, so a bad moment can't become the baseline."""
        c = self.calib
        if c.armed is not None:
            prefix = "Calibration restarted: " if self.restarted else "Waiting to calibrate: "
            if missing:
                self.ready_since = None
                c.message = prefix + ", ".join(missing)
            else:
                self.ready_since = now if self.ready_since is None else self.ready_since
                c.message = prefix + "hold still..."
                if now - self.ready_since >= READY_HOLD_S:
                    c.start(now, 0, c.armed)
                    self.not_ready_since = None
                    self.restarted = False
        elif c.recording and not c.waiting(now) and self.ready_since is not None:
            # only "when ready" calibrations (ready_since set) are restarted
            if missing:
                self.not_ready_since = now if self.not_ready_since is None else self.not_ready_since
                if now - self.not_ready_since >= READY_ABORT_S:
                    c.arm(c.until - c.begins, "Calibration restarted: " + ", ".join(missing))
                    self.ready_since = self.not_ready_since = None
                    self.restarted = True
            else:
                self.not_ready_since = None

    def process(self, msg, extra_lines=(), extra_alerts=()):
        """One `frame` message -> one `state` message. extra_* come from the audio side."""
        now, w, h = msg["t"], msg["width"], msg["height"]
        if self.scoring and (now < self.last_t or now - self.last_t > HAND_GRACE_S):
            self._incident_since.clear()
            self._counted_incidents.clear()
        self.last_t = now
        pose = points(msg["pose"]) if msg.get("pose") else None
        hands = msg.get("hands") or []
        face = msg.get("face")
        face_visible = face_in_frame(face)
        shoulders_visible = shoulders_in_frame(pose)
        framing_bad = not (face_visible and shoulders_visible)

        lines, rest_circles, hand_labels = [], [], []
        tilt_bad = down_bad = fast = still = repeating = False
        both_hands = len(hands) >= 2
        if both_hands:
            self.both_seen_at = now
        # brief dropouts (motion blur) don't count as a missing hand for still / repeat checks
        both_recent = now - self.both_seen_at <= HAND_GRACE_S
        tilt = pitch = eyes = None
        wrists = [None, None]
        base = self.calib.baseline
        calibrating = self.calib.busy
        hand_points = [points(hand["landmarks"]) for hand in hands]

        # Shoulder-relative hand coordinates only make sense when both shoulders are tracked.
        # Without that stable reference, omit hand-motion scoring for this frame.
        shoulders_visible = bool(pose and all(pose[i].visibility > 0.5 for i in (L_SHOULDER, R_SHOULDER)))

        # shoulders + hand motion (pose)
        if shoulders_visible:
            lm = pose
            tilt, width = shoulder_tilt(lm, w, h)
            if base and shoulders_visible:
                off = tilt - base["tilt"]
                tilt_bad = abs(off) > self.limits["tilt"]
                lines.append(line(f"shoulder tilt: {tilt:+.1f} deg  ({off:+.1f} vs calibrated)",
                                  "red" if tilt_bad else "white"))
            else:
                lines.append(line(f"shoulder tilt: {tilt:+.1f} deg"))

            # wrists in shoulder widths from the shoulder midpoint
            mx, my = (lm[L_SHOULDER].x + lm[R_SHOULDER].x) / 2 * w, (lm[L_SHOULDER].y + lm[R_SHOULDER].y) / 2 * h
            wrists = [((lm[i].x * w - mx) / width, (lm[i].y * h - my) / width) if lm[i].visibility > 0.5 else None
                      for i in (L_WRIST, R_WRIST)]
            pose_wrists_px = [np.array((lm[i].x * w, lm[i].y * h)) if wrists[j] else None
                              for j, i in enumerate((L_WRIST, R_WRIST))]
            self.motion.add(now, wrists, match_hands(hand_points, pose_wrists_px, w, h))
            speed, busy = self.motion.speed(0.3), self.motion.activity(5.0)
            fast = bool(hands) and (speed > self.limits["fast"] or busy > HAND_BUSY)
            # at rest = every hand with a calibrated rest spot is in view and within REST_RADIUS of it
            rests = [base[k] if base else None for k in ("rest_l", "rest_r")]
            at_rest = any(rests) and all(p and math.dist(r, p) < REST_RADIUS for r, p in zip(rests, wrists) if r)
            # resting wrist positions only wobble, so at rest only finger motion is checked for repeats
            repeat, repeat_where = self.motion.repetition(wrists=not at_rest)
            for r in rests:
                if r:
                    rest_circles.append({"x": round((mx + r[0] * width) / w, 4), "y": round((my + r[1] * width) / h, 4),
                                         "r": round(REST_RADIUS * width / w, 4), "active": bool(at_rest)})
            # Score a gesture only when both hands are visible. A single tracked wrist is too
            # easy to confuse with an arm that has simply left the frame.
            still = both_recent and (self.motion.activity(1.0) < HAND_STILL or at_rest)
            repeating = both_recent and repeat > self.limits["repeat"]
            still_s = now - self.still_alert.bad_since if self.still_alert.bad_since else 0
            lines.append(line(f"hands: speed {speed:.1f}  avg {busy:.1f}  repeat {repeat:.2f}"
                              + (f" ({repeat_where})" if repeat_where and repeat > 0.3 else "")
                              + (f"  still {still_s:.0f}s" if still_s >= 1 else "")
                              + ("  at rest" if at_rest else ""),
                              "red" if fast or repeating or still_s > HAND_STILL_S else "white"))
        else:
            self.motion.clear()
            lines.append(line("no body detected - step back so shoulders are in frame", "red"))

        # hand angle, shown next to each wrist
        for hand, pts in zip(hands, hand_points):
            angle = hand_angle(pts, w, h)
            hand_labels.append({"x": pts[HAND_WRIST].x, "y": pts[HAND_WRIST].y,
                                "text": f"{hand['handedness'][0]} {angle:+.0f} deg"})

        # eye level (face landmarks + blendshapes)
        if face_visible:
            pitch = head_pitch(points(face["landmarks"]), w, h)
            eyes = eyes_down(face["blendshapes"])
            if base and "pitch" in base:
                down, eyes_off = pitch - base["pitch"], eyes - base["eyes"]
                down_bad = down > self.limits["look_down"] or eyes_off > EYES_DOWN_MAX
                lines.append(line(f"eye level: head {down:+.0f} deg down, eyes down {eyes_off:+.2f}  (vs calibrated)",
                                  "red" if down_bad else "white"))
            else:
                lines.append(line(f"eye level: head pitch {pitch:+.0f} deg, eyes down {eyes:.2f}"))
        else:
            lines.append(line("face not fully visible", "red"))

        lines.extend(extra_lines)

        c = self.calib
        if c.recording and now >= c.until:
            # Commit the completed window before considering readiness on the next frame.
            duration = c.until - c.begins
            c.add(now)
            if c.baseline:
                for alert in (self.tilt_alert, self.down_alert, self.still_alert):
                    alert.reset()
            elif self.ready_since is not None:
                c.arm(duration, "Calibration restarted: get in frame and hold still")
                self.ready_since = self.not_ready_since = None
                self.restarted = True
        elif c.busy:
            missing = self._readiness(now, pose, face_visible, shoulders_visible, both_recent, w, h)
            self._update_calibration(now, missing)
            if not missing or self.ready_since is None:
                c.add(now, tilt=tilt if shoulders_visible else None, pitch=pitch, eyes=eyes,
                      rest_l=wrists[0], rest_r=wrists[1])

        # alerts are suppressed while calibrating so getting into position doesn't trigger them
        alerts = []
        if self.scoring and (self.scoring_started_at is None or now >= self.scoring_started_at):
            conditions = {
                "face": framing_bad,
                "tilt": tilt_bad if base and shoulders_visible else None,
                "look_down": down_bad if base and face_visible else None,
                "hands_missing": not both_hands,
                "still": still if pose and both_recent else None,
                "repeat": repeating if pose and both_recent else None,
                "fast": fast if hands and any(wrists) else None,
            }
            self._score_frame(now, dict.fromkeys(conditions) if calibrating else conditions)
        if not self.calib.busy:
            checks = ((self.face_alert, framing_bad), (self.tilt_alert, tilt_bad), (self.down_alert, down_bad),
                      (self.missing_alert, not both_hands),
                      (self.still_alert, still), (self.fast_alert, fast), (self.repeat_alert, repeating))
            alerts = [a.text for a, bad in checks if a.update(bad, now)]
        # voice / filler / script alerts don't depend on the posture calibration: only hidden during
        # the 3 s recording itself, not while it waits for the speaker to get in frame (which can last)
        if not self.calib.recording:
            alerts.extend(extra_alerts)

        return {"type": "state", "t": now, "lines": lines, "alerts": alerts,
                "rest_circles": rest_circles, "hand_labels": hand_labels,
                "calibration": {**self.calib.state(now), "request_id": self.calibration_request_id}}
