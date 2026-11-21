"""
Webcam analysis: posture, eye level and hand motion from MediaPipe landmarks sent by the
Electron app. Ported from experiments/mediapipe_webcam/webcam_test.py (same measurements and
thresholds), minus OpenCV/MediaPipe: Electron runs the models and draws the overlay this returns.

  - shoulder tilt (degrees)          -> "Level your shoulders"  (vs calibration)
  - eye level: head pitch + eye gaze -> "Look up"               (vs calibration)
  - face visible                     -> "Face the camera"
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

# Thresholds (guesses - tune on real recordings). Tilt and eye level are deviations from calibration.
TILT_MAX_DEG = 6            # shoulder tilt this far from calibrated = uneven
LOOK_DOWN_MAX_DEG = 15      # head pitched down this far past calibrated = looking down
EYES_DOWN_MAX = 0.35        # eyeLookDown blendshape score (0-1) this far above calibrated = eyes down
# Hand thresholds are absolute (not calibrated). Wrist positions are in shoulder widths from the
# shoulder midpoint, so distance to the camera and body sway don't matter.
FACE_MISSING_S = 1.0        # no face seen for this long = warn
HANDS_MISSING_S = 1.5       # fewer than 2 hands seen for this long = warn
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
CALIBRATE_DELAY_S = 2.0     # after clicking Calibrate, wait this long so you can get into position
CALIBRATE_S = 2.0           # calibration then records this long and keeps the median of each measurement


class Alert:
    """Fires once a condition has held for `hold_s`, stays on screen `show_s` after it clears."""

    def __init__(self, text, hold_s, show_s=1.5):
        self.text, self.hold_s, self.show_s = text, hold_s, show_s
        self.bad_since = None
        self.visible_until = 0.0

    def update(self, bad, now):
        if not bad:
            self.bad_since = None
        elif self.bad_since is None:
            self.bad_since = now
        elif now - self.bad_since >= self.hold_s:
            self.visible_until = now + self.show_s
        return now < self.visible_until

    def reset(self):
        self.bad_since, self.visible_until = None, 0.0


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
    """After start(), waits CALIBRATE_DELAY_S, records each measurement for CALIBRATE_S, then keeps
    the medians as the baseline."""

    KEYS = ("tilt", "pitch", "eyes")              # required
    OPTIONAL = ("rest_l", "rest_r")               # resting wrist (x, y); None if resting out of frame

    def __init__(self):
        self.baseline = None          # {"tilt": deg, "pitch": deg, "eyes": score, "rest_l"/"rest_r": (x, y) or None}
        self.begins = None            # recording starts at this time (after the get-into-position pause)
        self.until = None             # recording ends at this time
        self.samples = {}
        self.message = "Stand normally, look at the camera, then calibrate"

    def start(self, now):
        self.begins = now + CALIBRATE_DELAY_S
        self.until = self.begins + CALIBRATE_S
        self.samples = {k: [] for k in self.KEYS + self.OPTIONAL}

    @property
    def recording(self):
        """True for the whole calibration, including the pause before recording."""
        return self.until is not None

    def waiting(self, now):
        """True during the get-into-position pause."""
        return self.recording and now < self.begins

    def add(self, now, **values):
        """Feed this frame's measurements (None = not seen). Returns True when recording just finished."""
        if not self.recording or self.waiting(now):
            return False
        for k, v in values.items():
            if v is not None:
                self.samples[k].append(v)
        if now < self.until:
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
        self.message = ("Calibrated: tilt {tilt:+.1f} deg, head pitch {pitch:+.1f} deg, eyes down {eyes:.2f}, ".format(
            **self.baseline) + f"resting hands in frame: {rest}/2")
        return True

    def state(self, now):
        phase = "waiting" if self.waiting(now) else "recording" if self.recording else "idle"
        if phase == "waiting":
            self.message = "Get into position - stand normally, hands at rest, look at the camera"
        elif phase == "recording":
            self.message = "Recording calibration - hold still"
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
        self.face_alert = Alert("Face the camera", hold_s=FACE_MISSING_S)
        self.missing_alert = Alert("Show both hands or step back", hold_s=HANDS_MISSING_S)
        self.still_alert = Alert("Use your hands", hold_s=HAND_STILL_S)
        self.fast_alert = Alert("Slow your hands", hold_s=0.4)
        self.repeat_alert = Alert("Vary your gestures", hold_s=1.0)
        self.calib = Calibration()
        self.motion = HandMotion()
        self.both_seen_at = -math.inf       # last time both hands were detected
        self.last_t = 0.0

    def calibrate(self, t=None):
        """Start calibration. Timed by the frames that follow; `t` defaults to the last frame's time."""
        if not self.calib.recording:
            self.calib.start(self.last_t if t is None else t)

    def process(self, msg, extra_lines=(), extra_alerts=()):
        """One `frame` message -> one `state` message. extra_* come from the audio side."""
        now, w, h = msg["t"], msg["width"], msg["height"]
        self.last_t = now
        pose = points(msg["pose"]) if msg.get("pose") else None
        hands = msg.get("hands") or []
        face = msg.get("face")

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
        hand_points = [points(hand["landmarks"]) for hand in hands]

        # shoulders + hand motion (pose)
        if pose:
            lm = pose
            tilt, width = shoulder_tilt(lm, w, h)
            if base:
                off = tilt - base["tilt"]
                tilt_bad = abs(off) > TILT_MAX_DEG
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
            fast = speed > HAND_FAST or busy > HAND_BUSY
            # at rest = every hand with a calibrated rest spot is in view and within REST_RADIUS of it
            rests = [base[k] if base else None for k in ("rest_l", "rest_r")]
            at_rest = any(rests) and all(p and math.dist(r, p) < REST_RADIUS for r, p in zip(rests, wrists) if r)
            # resting wrist positions only wobble, so at rest only finger motion is checked for repeats
            repeat, repeat_where = self.motion.repetition(wrists=not at_rest)
            for r in rests:
                if r:
                    rest_circles.append({"x": round((mx + r[0] * width) / w, 4), "y": round((my + r[1] * width) / h, 4),
                                         "r": round(REST_RADIUS * width / w, 4), "active": bool(at_rest)})
            # stillness and repetition only count when both hands are actually in view
            still = both_recent and (self.motion.activity(1.0) < HAND_STILL or at_rest)
            repeating = both_recent and repeat > REPEAT_SCORE
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
        if face:
            pitch = head_pitch(points(face["landmarks"]), w, h)
            eyes = eyes_down(face["blendshapes"])
            if base:
                down, eyes_off = pitch - base["pitch"], eyes - base["eyes"]
                down_bad = down > LOOK_DOWN_MAX_DEG or eyes_off > EYES_DOWN_MAX
                lines.append(line(f"eye level: head {down:+.0f} deg down, eyes down {eyes_off:+.2f}  (vs calibrated)",
                                  "red" if down_bad else "white"))
            else:
                lines.append(line(f"eye level: head pitch {pitch:+.0f} deg, eyes down {eyes:.2f}"))
        else:
            lines.append(line("no face detected", "red"))

        lines.extend(extra_lines)

        if self.calib.add(now, tilt=tilt, pitch=pitch, eyes=eyes, rest_l=wrists[0], rest_r=wrists[1]) and self.calib.baseline:
            for a in (self.tilt_alert, self.down_alert, self.still_alert):
                a.reset()

        # alerts are suppressed while calibrating so getting into position doesn't trigger them
        alerts = []
        if not self.calib.recording:
            checks = ((self.face_alert, not face), (self.tilt_alert, tilt_bad), (self.down_alert, down_bad),
                      (self.missing_alert, not both_hands),
                      (self.still_alert, still), (self.fast_alert, fast), (self.repeat_alert, repeating))
            alerts = [a.text for a, bad in checks if a.update(bad, now)]
            alerts.extend(extra_alerts)

        return {"type": "state", "t": now, "lines": lines, "alerts": alerts,
                "rest_circles": rest_circles, "hand_labels": hand_labels,
                "calibration": self.calib.state(now)}
