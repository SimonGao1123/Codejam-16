"""
Webcam test for MediaPipe pose + hand + face tracking.

Shows the skeleton on your webcam feed plus live numbers, and an alert banner
when one stays past its threshold:
  - shoulder tilt (degrees)          -> "Level your shoulders"  (vs calibration)
  - eye level: head pitch + eye gaze -> "Look up"               (vs calibration)
  - hand angle (per hand) + speed    -> "Slow your hands"

Calibration: stand the way you normally present, looking at the camera, then
click "Calibrate" (or press c). It records your measurements for a couple of
seconds and alerts when you drift too far from them. Posture and eye-level
alerts stay off until you calibrate.

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
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

MODEL_DIR = Path(__file__).resolve().parents[2] / "models"
MODELS = {
    "pose": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
    "hand": "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
    "face": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
}

POSE_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 7), (0, 4), (4, 5), (5, 6), (6, 8), (9, 10),
    (11, 12), (11, 13), (13, 15), (15, 17), (15, 19), (15, 21), (17, 19),
    (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20),
    (11, 23), (12, 24), (23, 24), (23, 25), (24, 26), (25, 27), (26, 28),
    (27, 29), (28, 30), (29, 31), (30, 32), (27, 31), (28, 32),
]
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
HAND_FAST = 3.0             # wrist speed in shoulder-widths per second (absolute, not calibrated)
SPEED_WINDOW_S = 0.3        # speed is measured over this much history
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


def draw(frame, landmarks, connections, color):
    h, w = frame.shape[:2]
    pts = [(int(p.x * w), int(p.y * h)) for p in landmarks]
    for a, b in connections:
        cv2.line(frame, pts[a], pts[b], color, 2)
    for p in pts:
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


def wrist_speed(history, width):
    """Fastest wrist's speed over the history window, in shoulder-widths per second."""
    if len(history) < 2:
        return 0.0
    (t0, w0), (t1, w1) = history[0], history[-1]
    moved = [math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(w0, w1) if a and b]
    return max(moved, default=0.0) / width / max(t1 - t0, 1e-6)


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

    KEYS = ("tilt", "pitch", "eyes")

    def __init__(self):
        self.baseline = None          # {"tilt": deg, "pitch": deg, "eyes": score} once calibrated
        self.until = None             # recording ends at this time
        self.samples = {}
        self.message = "Stand normally, look at the camera, then calibrate"

    def start(self, now):
        self.until = now + CALIBRATE_S
        self.samples = {k: [] for k in self.KEYS}

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
        self.baseline = {k: statistics.median(v) for k, v in self.samples.items()}
        self.message = "Calibrated: tilt {tilt:+.1f} deg, head pitch {pitch:+.1f} deg, eyes down {eyes:.2f}".format(
            **self.baseline)
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
    hands_alert = Alert("Slow your hands", hold_s=0.4)

    calib = Calibration()
    wrist_history = deque()               # (time, [wrist xy or None, wrist xy or None])
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

        tilt_bad = down_bad = fast = False
        tilt = pitch = eyes = None
        base = calib.baseline

        # shoulders + hand speed (pose)
        if pose_res.pose_landmarks:
            lm = pose_res.pose_landmarks[0]
            draw(frame, lm, POSE_CONNECTIONS, GREEN)
            tilt, width = shoulder_tilt(lm, w, h)
            y += 26
            if base:
                off = tilt - base["tilt"]
                tilt_bad = abs(off) > TILT_MAX_DEG
                label(frame, y, f"shoulder tilt: {tilt:+.1f} deg  ({off:+.1f} vs calibrated)",
                      RED if tilt_bad else WHITE)
            else:
                label(frame, y, f"shoulder tilt: {tilt:+.1f} deg")

            # wrist speed normalized by shoulder width so distance to camera doesn't matter
            wrists = [(lm[i].x * w, lm[i].y * h) if lm[i].visibility > 0.5 else None
                      for i in (L_WRIST, R_WRIST)]
            wrist_history.append((now, wrists))
            while now - wrist_history[0][0] > SPEED_WINDOW_S:
                wrist_history.popleft()
            speed = wrist_speed(wrist_history, width)
            fast = speed > HAND_FAST
            y += 26
            label(frame, y, f"hand speed: {speed:.1f} shoulder-widths/s", RED if fast else WHITE)
        else:
            wrist_history.clear()
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
            label(frame, y, "no face detected", YELLOW)

        if calib.add(now, tilt=tilt, pitch=pitch, eyes=eyes) and calib.baseline:
            for a in (tilt_alert, down_alert):
                a.bad_since, a.visible_until = None, 0.0

        # alerts are suppressed while recording so calibrating doesn't trigger them
        if not calib.recording:
            shown = [a.text for a, bad in ((tilt_alert, tilt_bad), (down_alert, down_bad), (hands_alert, fast))
                     if a.update(bad, now)]
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
            calib.message = "Recording calibration - stand normally and look at the camera"
        clicked["calibrate"] = False

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
