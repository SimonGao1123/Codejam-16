"""Webcam frames the size and shape MediaPipe's really are: a speaker facing the camera, gesturing."""

import json
import math

import numpy as np

BLENDSHAPES = ["eyeLookDownLeft", "eyeLookDownRight"] + [f"blendshapeNumber{i:02d}" for i in range(50)]


def frames(seconds, fps=30, seed=0):
    rng = np.random.default_rng(seed)
    body = rng.uniform(0.3, 0.7, (33, 2))
    body[[11, 12]] = [[0.4, 0.6], [0.6, 0.6]]
    face = rng.uniform(0.42, 0.58, (478, 3))
    face[[10, 152, 234, 454], :2] = [[0.5, 0.2], [0.5, 0.45], [0.42, 0.33], [0.58, 0.33]]
    r = lambda a: np.round(a, 4).tolist()                                    # noqa: E731
    for k in range(int(seconds * fps)):
        t = k / fps
        jitter = lambda n, d: rng.normal(0, 0.001, (n, d))                   # noqa: E731
        pose = np.column_stack([body + jitter(33, 2), rng.normal(0, 0.1, 33), np.full(33, 0.9)])
        hands = []
        for side, x in (("Left", 0.3), ("Right", 0.7)):
            wrist = np.array([x + 0.08 * math.sin(2.1 * t + x), 0.75 + 0.06 * math.sin(1.3 * t)])
            pose[16 if side == "Left" else 15, :2] = wrist
            hand = np.column_stack([wrist + rng.uniform(-0.04, 0.04, (21, 2)), rng.normal(0, 0.05, 21)])
            hand[0, :2] = wrist
            hands.append({"handedness": side, "score": 0.9712, "landmarks": r(hand)})
        yield {"type": "frame", "t": t, "width": 1280, "height": 720, "pose": r(pose), "hands": hands,
               "face": {"landmarks": r(face + jitter(478, 3)),
                        "blendshapes": {b: round(float(v), 4) for b, v in zip(BLENDSHAPES, rng.random(52))}}}


def wire(frame):
    """As JSON.stringify writes it (no spaces)."""
    return json.dumps(frame, separators=(",", ":"))


def compact(frame):
    """What the app sends of it (compactFrame in webcamProtocol.ts)."""
    from presentation_service.webcam_analysis import compact_frame
    out = compact_frame({**frame, "t": round(frame["t"], 4)})
    if out["face"]:
        out["face"]["points"] = [p[:3 if i < 2 else 2] for i, p in enumerate(out["face"]["points"])]
    return out
