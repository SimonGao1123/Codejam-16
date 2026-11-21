"""
Webcam test for MediaPipe pose + hand + face tracking.

Shows the skeleton on your webcam feed plus live numbers, and an alert banner
when one stays past its threshold:
  - shoulder tilt (degrees)          -> "Level your shoulders"  (vs calibration)
  - eye level: head pitch + eye gaze -> "Look up"               (vs calibration)
  - face visible                     -> "Face the camera"
  - both hands visible               -> "Show both hands or step back"
  - hand motion: none for ~10 s      -> "Use your hands"
                 too fast / too busy -> "Slow your hands"
                 same gesture looping-> "Vary your gestures"
  - hand angle (per hand)            -> shown next to each wrist, no alert

Calibration: stand the way you normally present, looking at the camera, with
your hands in their resting position, then click "Calibrate" (or press c). It
records your measurements for a couple of seconds and alerts when you drift too
far from them. Hands resting at that spot for ~10 s also counts as not using
them. Posture and eye-level alerts stay off until you calibrate; the other hand
alerts work right away.

Run:   uv run webcam_test.py
       uv run webcam_test.py --camera 1     (if the wrong camera opens)
Keys:  q = quit,  c = calibrate
"""

import argparse
import math
import statistics
import time
import urllib.request
from collections import deque
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

MODEL_DIR = Path(__file__).resolve().parents[2] / "models"
MODELS = {
    "pose": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
    "hand": "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
    "face": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
}

POSE_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 7), (0, 4), (4, 5), (5, 6), (6, 8),
    (11, 12), (11, 13), (13, 15), (15, 17), (15, 19), (15, 21), (17, 19),
    (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20),
    (11, 23), (12, 24), (23, 24), (23, 25), (24, 26), (25, 27), (26, 28),
    (27, 29), (28, 30), (29, 31), (30, 32), (27, 31), (28, 32),
]
POSE_MOUTH = (9, 10)        # mouth corners: not used, so not drawn
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
]

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
HAND_FAST = 3.0             # wrist speed over the last 0.3 s above this = too fast
HAND_BUSY = 1.5             # average wrist speed over the last 5 s above this = too much motion
REPEAT_WINDOW_S = 6.0       # repetition is checked over this much wrist history
REPEAT_SCORE = 0.6          # autocorrelation peak (0-1) above this = same gesture looping
REPEAT_MIN_AMP = 0.08       # ignore tiny motions (shoulder widths) when checking for repetition
REPEAT_LAGS_S = (0.4, 2.5)  # a repeated gesture takes between this many seconds per cycle
CALIBRATE_S = 2.0           # calibration records this long and keeps the median of each measurement
BUTTON = (12, -60, 230, -16)  # calibrate button: x0, y0, x1, y1 (negative y = from frame bottom)

GREEN, YELLOW, RED, WHITE = (80, 200, 80), (0, 200, 255), (60, 60, 230), (255, 255, 255)


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


def get_model(name):
    path = MODEL_DIR / f"{name}.task"
    if not path.exists():
        MODEL_DIR.mkdir(exist_ok=True)
        print(f"downloading {name} model ...")
        urllib.request.urlretrieve(MODELS[name], path)
    return str(path)


def make_landmarkers():
    pose = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=get_model("pose")),
        running_mode=vision.RunningMode.VIDEO, num_poses=1))
    hands = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=get_model("hand")),
        running_mode=vision.RunningMode.VIDEO, num_hands=2))
    face = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=get_model("face")),
        running_mode=vision.RunningMode.VIDEO, num_faces=1, output_face_blendshapes=True))
    return pose, hands, face


def draw(frame, landmarks, connections, color, skip=()):
    h, w = frame.shape[:2]
    pts = [(int(p.x * w), int(p.y * h)) for p in landmarks]
    for a, b in connections:
        cv2.line(frame, pts[a], pts[b], color, 2)
    for i, p in enumerate(pts):
        if i not in skip:
            cv2.circle(frame, p, 3, WHITE, -1)


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
    scores = {b.category_name: b.score for b in blendshapes}
    return (scores.get("eyeLookDownLeft", 0) + scores.get("eyeLookDownRight", 0)) / 2


def hand_angle(hand, w, h):
    """Angle of the wrist -> middle knuckle line: 0 = fingers up, +90 = right, -90 = left."""
    a, b = hand[HAND_WRIST], hand[HAND_MIDDLE_MCP]
    return math.degrees(math.atan2((b.x - a.x) * w, -(b.y - a.y) * h))


class HandMotion:
    """Keeps a few seconds of wrist positions and measures speed, busyness and repetition."""

    RATE = 15                       # Hz; history is resampled to this for the repetition check

    def __init__(self):
        self.history = deque()      # (time, [(x, y) or None, (x, y) or None])
        self.speeds = deque()       # (time, speed over the last 0.3 s)

    def clear(self):
        self.history.clear()
        self.speeds.clear()

    def add(self, now, wrists):
        self.history.append((now, wrists))
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
            seen = [(t, w[i]) for t, w in recent if w[i]]
            if len(seen) >= 2:
                (t0, a), (t1, b) = seen[0], seen[-1]
                best = max(best, math.hypot(a[0] - b[0], a[1] - b[1]) / max(t1 - t0, 1e-6))
        return best

    def activity(self, window):
        """Average of the 0.3 s speeds over the last `window` seconds (max 5 s). Unlike speed(window),
        a gesture that ends where it started still counts as motion."""
        recent = [v for t, v in self.speeds if self.speeds[-1][0] - t <= window]
        return sum(recent) / len(recent) if recent else 0.0

    def repetition(self):
        """0-1: how strongly either wrist's path repeats itself (autocorrelation peak)."""
        if not self.history or self.history[-1][0] - self.history[0][0] < 0.8 * REPEAT_WINDOW_S:
            return 0.0
        times = np.array([t for t, _ in self.history])
        grid = np.arange(times[0], times[-1], 1 / self.RATE)
        lo, hi = (int(s * self.RATE) for s in REPEAT_LAGS_S)
        best = 0.0
        for i in range(2):
            mask = np.array([w[i] is not None for _, w in self.history])
            if mask.mean() < 0.7:
                continue
            pts = np.array([w[i] for _, w in self.history if w[i]])
            x = np.interp(grid, times[mask], pts[:, 0])
            y = np.interp(grid, times[mask], pts[:, 1])
            x, y = x - x.mean(), y - y.mean()
            energy = (x * x + y * y).sum()
            if math.sqrt(energy / len(grid)) < REPEAT_MIN_AMP:
                continue
            n = len(grid)
            ac = np.array([(x[:n - k] * x[k:] + y[:n - k] * y[k:]).sum() / energy * n / (n - k)
                           for k in range(min(hi + 2, n - 1))])
            # a real repeat is a peak that comes after the correlation has dropped off
            dipped = False
            for k in range(1, len(ac) - 1):
                dipped = dipped or ac[k] < 0.3
                if dipped and lo <= k <= hi and ac[k] >= ac[k - 1] and ac[k] >= ac[k + 1]:
                    best = max(best, float(ac[k]))
        return min(best, 1.0)


def label(frame, y, text, color=WHITE):
    cv2.putText(frame, text, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
    cv2.putText(frame, text, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)


def banner(frame, i, text):
    """Big red alert box, centered near the top; i stacks multiple alerts."""
    w = frame.shape[1]
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 1.2, 3)
    x, y = (w - tw) // 2, 150 + i * (th + 30)
    cv2.rectangle(frame, (x - 16, y - th - 14), (x + tw + 16, y + 14), RED, -1)
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1.2, WHITE, 3)


def button_rect(frame):
    h = frame.shape[0]
    x0, y0, x1, y1 = BUTTON
    return x0, h + y0, x1, h + y1


def draw_button(frame, text, active):
    x0, y0, x1, y1 = button_rect(frame)
    cv2.rectangle(frame, (x0, y0), (x1, y1), YELLOW if active else GREEN, -1)
    cv2.putText(frame, text, (x0 + 12, y1 - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)


class Calibration:
    """Records each measurement for CALIBRATE_S after start(), then keeps the medians as the baseline."""

    KEYS = ("tilt", "pitch", "eyes")              # required
    OPTIONAL = ("rest_l", "rest_r")               # resting wrist (x, y); None if resting out of frame

    def __init__(self):
        self.baseline = None          # {"tilt": deg, "pitch": deg, "eyes": score, "rest_l"/"rest_r": (x, y) or None}
        self.until = None             # recording ends at this time
        self.samples = {}
        self.message = "Stand normally, look at the camera, then calibrate"

    def start(self, now):
        self.until = now + CALIBRATE_S
        self.samples = {k: [] for k in self.KEYS + self.OPTIONAL}

    @property
    def recording(self):
        return self.until is not None

    def add(self, now, **values):
        """Feed this frame's measurements (None = not seen). Returns True when recording just finished."""
        if not self.recording:
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
        print(self.message)
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=0)
    args = parser.parse_args()

    pose, hands, face_lm = make_landmarkers()
    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        raise SystemExit("could not open webcam (check camera permission for your terminal)")

    window = "MediaPipe webcam test (q to quit)"
    cv2.namedWindow(window)
    clicked = {"calibrate": False, "rect": (0, 0, 0, 0)}

    def on_mouse(event, x, y, *_):
        x0, y0, x1, y1 = clicked["rect"]
        if event == cv2.EVENT_LBUTTONDOWN and x0 <= x <= x1 and y0 <= y <= y1:
            clicked["calibrate"] = True

    cv2.setMouseCallback(window, on_mouse)

    tilt_alert = Alert("Level your shoulders", hold_s=1.5)
    down_alert = Alert("Look up", hold_s=1.0)
    face_alert = Alert("Face the camera", hold_s=FACE_MISSING_S)
    missing_alert = Alert("Show both hands or step back", hold_s=HANDS_MISSING_S)
    still_alert = Alert("Use your hands", hold_s=HAND_STILL_S)
    fast_alert = Alert("Slow your hands", hold_s=0.4)
    repeat_alert = Alert("Vary your gestures", hold_s=1.0)

    calib = Calibration()
    motion = HandMotion()
    last_ts, fps = 0, 0.0
    prev = time.time()

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame = cv2.flip(frame, 1)        # mirror view; mediapipe handedness expects this
        h, w = frame.shape[:2]

        ts = max(int(time.monotonic() * 1000), last_ts + 1)   # must strictly increase
        last_ts = ts
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        pose_res = pose.detect_for_video(image, ts)
        hand_res = hands.detect_for_video(image, ts)
        face_res = face_lm.detect_for_video(image, ts)

        now = time.time()
        fps = 0.9 * fps + 0.1 / max(now - prev, 1e-6)
        prev = now
        y = 28
        label(frame, y, f"FPS {fps:.0f}   hands seen: {len(hand_res.hand_landmarks)}")

        tilt_bad = down_bad = fast = still = repeating = False
        both_hands = len(hand_res.hand_landmarks) >= 2
        tilt = pitch = eyes = None
        wrists = [None, None]
        base = calib.baseline

        # shoulders + hand speed (pose)
        if pose_res.pose_landmarks:
            lm = pose_res.pose_landmarks[0]
            draw(frame, lm, POSE_CONNECTIONS, GREEN, skip=POSE_MOUTH)
            tilt, width = shoulder_tilt(lm, w, h)
            y += 26
            if base:
                off = tilt - base["tilt"]
                tilt_bad = abs(off) > TILT_MAX_DEG
                label(frame, y, f"shoulder tilt: {tilt:+.1f} deg  ({off:+.1f} vs calibrated)",
                      RED if tilt_bad else WHITE)
            else:
                label(frame, y, f"shoulder tilt: {tilt:+.1f} deg")

            # wrists in shoulder widths from the shoulder midpoint
            mx, my = (lm[L_SHOULDER].x + lm[R_SHOULDER].x) / 2 * w, (lm[L_SHOULDER].y + lm[R_SHOULDER].y) / 2 * h
            wrists = [((lm[i].x * w - mx) / width, (lm[i].y * h - my) / width) if lm[i].visibility > 0.5 else None
                      for i in (L_WRIST, R_WRIST)]
            motion.add(now, wrists)
            speed, busy, repeat = motion.speed(0.3), motion.activity(5.0), motion.repetition()
            fast = speed > HAND_FAST or busy > HAND_BUSY
            # at rest = every hand with a calibrated rest spot is in view and within REST_RADIUS of it
            rests = [base[k] if base else None for k in ("rest_l", "rest_r")]
            at_rest = any(rests) and all(p and math.dist(r, p) < REST_RADIUS for r, p in zip(rests, wrists) if r)
            for r in rests:
                if r:
                    cv2.circle(frame, (int(mx + r[0] * width), int(my + r[1] * width)), int(REST_RADIUS * width),
                               GREEN if at_rest else WHITE, 1)
            # stillness and repetition only count when both hands are actually in view
            still = both_hands and (motion.activity(1.0) < HAND_STILL or at_rest)
            repeating = both_hands and repeat > REPEAT_SCORE
            still_s = now - still_alert.bad_since if still_alert.bad_since else 0
            y += 26
            label(frame, y, f"hands: speed {speed:.1f}  avg {busy:.1f}  repeat {repeat:.2f}"
                            + (f"  still {still_s:.0f}s" if still_s >= 1 else "")
                            + ("  at rest" if at_rest else ""),
                  RED if fast or repeating or still_s > HAND_STILL_S else WHITE)
        else:
            motion.clear()
            y += 26
            label(frame, y, "no body detected - step back so shoulders are in frame", RED)

        # hand angle (hand landmarks), drawn next to each wrist
        for hand, handed in zip(hand_res.hand_landmarks, hand_res.handedness):
            draw(frame, hand, HAND_CONNECTIONS, YELLOW)
            angle = hand_angle(hand, w, h)
            wx, wy = int(hand[HAND_WRIST].x * w), int(hand[HAND_WRIST].y * h)
            cv2.putText(frame, f"{handed[0].category_name[0]} {angle:+.0f} deg", (wx + 10, wy + 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, YELLOW, 2)

        # eye level (face landmarks + blendshapes)
        if face_res.face_landmarks:
            pitch = head_pitch(face_res.face_landmarks[0], w, h)
            eyes = eyes_down(face_res.face_blendshapes[0])
            y += 26
            if base:
                down, eyes_off = pitch - base["pitch"], eyes - base["eyes"]
                down_bad = down > LOOK_DOWN_MAX_DEG or eyes_off > EYES_DOWN_MAX
                label(frame, y, f"eye level: head {down:+.0f} deg down, eyes down {eyes_off:+.2f}  (vs calibrated)",
                      RED if down_bad else WHITE)
            else:
                label(frame, y, f"eye level: head pitch {pitch:+.0f} deg, eyes down {eyes:.2f}")
        else:
            y += 26
            label(frame, y, "no face detected", RED)

        if calib.add(now, tilt=tilt, pitch=pitch, eyes=eyes, rest_l=wrists[0], rest_r=wrists[1]) and calib.baseline:
            for a in (tilt_alert, down_alert, still_alert):
                a.bad_since, a.visible_until = None, 0.0

        # alerts are suppressed while recording so calibrating doesn't trigger them
        if not calib.recording:
            checks = ((face_alert, not face_res.face_landmarks), (tilt_alert, tilt_bad), (down_alert, down_bad),
                      (missing_alert, not both_hands),
                      (still_alert, still), (fast_alert, fast), (repeat_alert, repeating))
            shown = [a.text for a, bad in checks if a.update(bad, now)]
            for i, text in enumerate(shown):
                banner(frame, i, text)

        y += 26
        label(frame, y, calib.message, GREEN if calib.baseline else YELLOW)
        if calib.recording:
            draw_button(frame, f"Hold still... {calib.until - now:.1f}s", active=True)
        else:
            draw_button(frame, "Recalibrate (c)" if calib.baseline else "Calibrate (c)", active=False)
        clicked["rect"] = button_rect(frame)

        cv2.imshow(window, frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if (key == ord("c") or clicked["calibrate"]) and not calib.recording:
            calib.start(now)
            calib.message = "Recording calibration - stand normally, hands at rest, look at the camera"
        clicked["calibrate"] = False

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
