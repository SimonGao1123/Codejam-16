"""
Webcam test for MediaPipe pose + hand tracking.

Shows the skeleton on your webcam feed plus a few live numbers that the
real app would use for alerts:
  - shoulder tilt (degrees)    -> leaning / uneven posture
  - neck ratio                 -> drops when you slouch or push your head forward
  - hand motion                -> "still" for a while = no hand use

Run:   uv run webcam_test.py
       uv run webcam_test.py --camera 1     (if the wrong camera opens)
Keys:  q = quit,  c = calibrate posture baseline (sit/stand straight first)
"""

import argparse
import math
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

NOSE, L_SHOULDER, R_SHOULDER, L_WRIST, R_WRIST = 0, 11, 12, 15, 16
STILL_THRESHOLD = 0.15      # hand speed (shoulder-widths per second) below this = still
GREEN, YELLOW, RED, WHITE = (80, 200, 80), (0, 200, 255), (60, 60, 230), (255, 255, 255)


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
    return pose, hands


def draw(frame, landmarks, connections, color):
    h, w = frame.shape[:2]
    pts = [(int(p.x * w), int(p.y * h)) for p in landmarks]
    for a, b in connections:
        cv2.line(frame, pts[a], pts[b], color, 2)
    for p in pts:
        cv2.circle(frame, p, 3, WHITE, -1)


def posture_metrics(lm, w, h):
    """Shoulder tilt in degrees and neck ratio (nose height above shoulders / shoulder width)."""
    ls, rs, nose = lm[L_SHOULDER], lm[R_SHOULDER], lm[NOSE]
    dx, dy = (rs.x - ls.x) * w, (rs.y - ls.y) * h
    tilt = math.degrees(math.atan2(dy, dx))
    tilt = tilt - 180 if tilt > 90 else tilt + 180 if tilt < -90 else tilt
    width = math.hypot(dx, dy) or 1
    neck = ((ls.y + rs.y) / 2 - nose.y) * h / width
    return tilt, neck, width


def label(frame, y, text, color=WHITE):
    cv2.putText(frame, text, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
    cv2.putText(frame, text, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=0)
    args = parser.parse_args()

    pose, hands = make_landmarkers()
    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        raise SystemExit("could not open webcam (check camera permission for your terminal)")

    baseline_neck = None
    wrist_history = deque(maxlen=30)      # (time, wrist positions) for ~1s of motion
    still_since = time.time()
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

        for hand in hand_res.hand_landmarks:
            draw(frame, hand, HAND_CONNECTIONS, YELLOW)

        now = time.time()
        fps = 0.9 * fps + 0.1 / max(now - prev, 1e-6)
        prev = now
        y = 28
        label(frame, y, f"FPS {fps:.0f}   hands seen: {len(hand_res.hand_landmarks)}")

        if pose_res.pose_landmarks:
            lm = pose_res.pose_landmarks[0]
            draw(frame, lm, POSE_CONNECTIONS, GREEN)
            tilt, neck, width = posture_metrics(lm, w, h)

            y += 26
            label(frame, y, f"shoulder tilt: {tilt:+.1f} deg", RED if abs(tilt) > 6 else WHITE)

            y += 26
            if baseline_neck:
                drop = 1 - neck / baseline_neck
                label(frame, y, f"neck ratio: {neck:.2f}  ({drop:+.0%} vs baseline)",
                      RED if drop > 0.15 else WHITE)
            else:
                label(frame, y, f"neck ratio: {neck:.2f}  (press c to calibrate)", YELLOW)

            # hand motion: wrist speed normalized by shoulder width so distance to camera doesn't matter
            wrists = [(lm[i].x * w, lm[i].y * h) for i in (L_WRIST, R_WRIST)]
            wrist_history.append((now, wrists))
            speed = 0.0
            if len(wrist_history) > 1:
                (t0, w0), (t1, w1) = wrist_history[0], wrist_history[-1]
                moved = sum(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(w0, w1))
                speed = moved / width / max(t1 - t0, 1e-6)
            if speed > STILL_THRESHOLD:
                still_since = now
            still_for = now - still_since

            y += 26
            state = f"still {still_for:.0f}s" if still_for > 1 else "moving"
            label(frame, y, f"hand motion: {speed:.2f}  ({state})", RED if still_for > 10 else WHITE)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("c"):
                baseline_neck = neck
                print(f"calibrated: neck ratio baseline = {neck:.2f}")
        else:
            label(frame, y + 26, "no body detected - step back so shoulders are in frame", RED)
            key = cv2.waitKey(1) & 0xFF

        cv2.imshow("MediaPipe webcam test (q to quit)", frame)
        if key == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
