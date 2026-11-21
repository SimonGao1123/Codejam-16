import math

from presentation_service.webcam_analysis import WebcamAnalyzer

FPS = 30
BASE_HAND = [(-0.6, -0.9), (-0.3, -1.8), (0, -1.9), (0.4, -1.5), (0, -1)]   # tips + knuckle vs wrist


def hand(wx, wy, angle=0.0, handedness="Left"):
    """21 hand landmarks around a wrist at (wx, wy), fingers rotated by `angle` radians."""
    pts = [[wx, wy, 0.0]] * 21
    for idx, (dx, dy) in zip((4, 8, 12, 20, 9), BASE_HAND):
        c, s = math.cos(angle), math.sin(angle)
        pts[idx] = [wx + 0.05 * (c * dx - s * dy), wy + 0.05 * (s * dx + c * dy), 0.0]
    return {"handedness": handedness, "score": 0.9, "landmarks": pts}


def frame(t, lwrist=(0.4, 0.8), face=True, hands=2, flick=0.0):
    pose = [[0.5, 0.3, 0.0, 1.0] for _ in range(33)]
    pose[11], pose[12] = [0.4, 0.5, 0, 1], [0.6, 0.5, 0, 1]
    pose[15], pose[16] = [*lwrist, 0, 1], [0.6, 0.8, 0, 1]
    face_data = None
    if face:
        lm = [[0.5, 0.3, 0.0] for _ in range(478)]
        lm[10], lm[152] = [0.5, 0.2, -0.02], [0.5, 0.4, 0.02]
        face_data = {"landmarks": lm, "blendshapes": {"eyeLookDownLeft": 0.1, "eyeLookDownRight": 0.1}}
    hs = [hand(*lwrist, flick, "Left"), hand(0.6, 0.8, 0, "Right")][:hands]
    return {"type": "frame", "t": t, "width": 1280, "height": 720, "pose": pose, "hands": hs, "face": face_data}


def run(analyzer, secs, start=0.0, calibrate_at=None, **kw):
    """Feeds `secs` of frames; returns {alert text: first time shown} and the last state."""
    first, state = {}, None
    for i in range(int(secs * FPS)):
        t = start + i / FPS
        if calibrate_at is not None and abs(t - calibrate_at) < 0.5 / FPS:
            analyzer.calibrate(t)
        f = frame(t, **{k: (v(t) if callable(v) else v) for k, v in kw.items()})
        state = analyzer.process(f)
        for a in state["alerts"]:
            first.setdefault(a, round(t, 1))
    return first, state


def test_state_shape_and_lines():
    _, state = run(WebcamAnalyzer(), 1)
    assert state["type"] == "state" and state["calibration"]["phase"] == "idle"
    texts = [l["text"] for l in state["lines"]]
    assert texts[0].startswith("shoulder tilt") and texts[1].startswith("hands:") and texts[2].startswith("eye level")
    assert [l["text"] for l in state["hand_labels"]] == ["L +0 deg", "R +0 deg"]


def test_face_missing_alert_after_1s():
    first, _ = run(WebcamAnalyzer(), 4, face=lambda t: t < 2)
    assert first.get("Face the camera") == 3.0


def test_calibration_countdown_then_rest_circles_and_use_your_hands():
    a = WebcamAnalyzer()
    first, state = run(a, 16, calibrate_at=0.5)
    cal = state["calibration"]
    assert cal["calibrated"] and cal["phase"] == "idle" and "resting hands in frame: 2/2" in cal["message"]
    assert len(state["rest_circles"]) == 2 and all(c["active"] for c in state["rest_circles"])
    # 2 s countdown + 2 s recording, then hands at rest for 10 s
    assert 14.0 <= first["Use your hands"] <= 14.6
    assert "Level your shoulders" not in first and "Look up" not in first


def test_calibration_phases_reported():
    a = WebcamAnalyzer()
    a.process(frame(0.0))
    a.calibrate(0.0)
    assert a.process(frame(1.0))["calibration"]["phase"] == "waiting"
    assert a.process(frame(3.0))["calibration"]["phase"] == "recording"
    assert a.process(frame(4.1))["calibration"]["phase"] == "idle"


def test_repeated_wrist_flick_warns():
    flick = lambda t: 0.35 * math.sin(2 * math.pi * 1.5 * t) if t > 2 else 0.0  # noqa: E731
    first, _ = run(WebcamAnalyzer(), 12, flick=flick)
    assert "Vary your gestures" in first


def test_extra_lines_and_alerts_from_audio_side():
    state = WebcamAnalyzer().process(frame(0.0), extra_lines=[{"text": "voice: 180 Hz", "color": "white"}],
                                     extra_alerts=["Filler: um"])
    assert state["lines"][-1]["text"] == "voice: 180 Hz" and "Filler: um" in state["alerts"]
