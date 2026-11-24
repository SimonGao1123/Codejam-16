import math

from presentation_service.scoring import score_presentation
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
    assert first.get("Stay in frame") == 3.0


def test_one_sustained_face_absence_deducts_body_language_points():
    a = WebcamAnalyzer()
    a.start_scoring()
    run(a, 4, face=lambda t: t < 2)
    assert a.incidents["face"] == 1

    report = score_presentation(body_incidents=a.incidents, duration_seconds=300)
    face = report["categories"]["body_language"]["checks"]["face"]
    assert face["deduction"] == face["max_points"] == 6


def test_framing_counts_partial_or_distant_face_and_missing_shoulders():
    for bad_framing in ("cropped face", "distant face", "cropped shoulder"):
        a = WebcamAnalyzer()
        a.start_scoring()
        for i in range(2 * FPS):
            sample = frame(i / FPS)
            if bad_framing == "cropped face":
                sample["face"]["landmarks"][234][0] = 0.01
            elif bad_framing == "distant face":
                sample["face"]["landmarks"][152][1] = 0.23      # face 3% of the frame tall
            else:
                sample["pose"][11][0] = 0.01
            state = a.process(sample)
        assert "Stay in frame" in state["alerts"], bad_framing
        assert a.incidents["face"] == 1, bad_framing
        report = score_presentation(body_incidents=a.incidents)
        assert report["categories"]["body_language"]["deduction"] == 6


def test_first_two_framing_failures_each_deduct_the_face_points():
    counts = {"face": 1, "tilt": 0, "look_down": 0, "still": 1, "repeat": 0, "fast": 0}
    hands = {"still", "repeat", "fast"}
    report = score_presentation(body_incidents=counts, voice_incidents={"pitch": 0}, disabled_alerts=hands)
    body = report["categories"]["body_language"]
    # face 6 + tilt 2.5 + looking down 4 measured; one framing failure loses the face check's 6
    assert body["deduction"] == 6 and body["points"] == 6.5 and body["max_points"] == 12.5
    assert report["categories"]["voice_tone"]["points"] == 5
    assert [tip["deduction"] for tip in report["feedback"]] == [6]

    counts["face"] = 2
    report = score_presentation(body_incidents=counts, disabled_alerts=hands)
    body = report["categories"]["body_language"]
    # a second failure costs the face check's 6 again
    assert body["deduction"] == 12 and body["points"] == 0.5 and body["max_points"] == 12.5
    assert [tip["deduction"] for tip in report["feedback"]] == [12]

    report = score_presentation(body_incidents=counts, disabled_alerts=hands | {"face"})
    body = report["categories"]["body_language"]
    assert body["points"] == body["max_points"] == 6.5


def test_missing_hands_cost_enabled_hand_points_even_when_pose_reports_wrists():
    a = WebcamAnalyzer()
    run(a, 5, calibrate_at=0.5, hands=0)
    a.start_scoring()
    run(a, 1, start=5, hands=0)
    assert a.incidents["hands_missing"] == 0
    a.process(frame(6.0, hands=0))
    assert a.incidents == {"face": 0, "tilt": 0, "look_down": 0, "hands_missing": 1}
    report = score_presentation(body_incidents=a.incidents)
    body = report["categories"]["body_language"]
    assert body["points"] == 23.1 and body["max_points"] == 25      # 1.875 lost, shown as 1.9
    assert report["feedback"][0]["rule"] == "hands_missing"

    report = score_presentation(body_incidents=a.incidents, disabled_alerts={"fast"})
    body = report["categories"]["body_language"]
    assert body["points"] == 19.1 and body["max_points"] == 21

    report = score_presentation(body_incidents=a.incidents, disabled_alerts={"hands_missing"})
    body = report["categories"]["body_language"]
    assert body["points"] == body["max_points"] == 12.5

    run(a, 2, start=6.1, hands=0)
    assert a.incidents["hands_missing"] == 1
    a.process(frame(8.2))
    run(a, 1.1, start=8.5, hands=0)
    body = score_presentation(body_incidents=a.incidents)["categories"]["body_language"]
    assert a.incidents["hands_missing"] == 2 and body["points"] == 21.2


def test_hand_visibility_penalty_scales_with_session_length():
    counts = {"face": 0, "tilt": 0, "look_down": 0, "hands_missing": 1}
    short = score_presentation(body_incidents=counts, duration_seconds=18)
    long = score_presentation(body_incidents=counts, duration_seconds=17 * 60)
    repeated = score_presentation(body_incidents={**counts, "hands_missing": 17}, duration_seconds=17 * 60)
    assert short["categories"]["body_language"]["deduction"] == 1.9       # 1.875 per incident/min
    assert long["categories"]["body_language"]["deduction"] == 0.1        # 1.875 / 17
    assert repeated["categories"]["body_language"]["deduction"] == 1.9


def test_repeated_body_language_failures_lose_all_credit_for_the_check():
    report = score_presentation(body_incidents={"look_down": 1, "tilt": 2, "fast": 0},
                                duration_seconds=600)
    checks = report["categories"]["body_language"]["checks"]
    assert checks["look_down"]["deduction"] == 3                         # 0.75 of 4
    assert checks["tilt"]["deduction"] == 2.5                            # all of it
    assert checks["fast"]["deduction"] == 0


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
    state = WebcamAnalyzer().process(frame(0.0), extra_lines=[{"stat": "voice", "text": "voice: 180 Hz", "color": "white"}],
                                     extra_alerts=["Filler: um"])
    assert state["lines"][-1]["text"] == "voice: 180 Hz" and "Filler: um" in state["alerts"]


def rules(**changes):
    """Live alerts settings with defaults, plus {rule_id: {field: value}} changes."""
    base = {r: {"enabled": True, "threshold": None, "duration": d, "cooldown": 0}
            for r, d in (("tilt", 1.5), ("look_down", 1.0), ("face", 1.0), ("hands_missing", 1.0),
                         ("still", 10), ("fast", 0.4), ("repeat", 1.0))}
    for rule_id, change in changes.items():
        base[rule_id] = {**base[rule_id], **change}
    return base


def test_disabled_rule_never_shows():
    a = WebcamAnalyzer()
    a.apply_settings(rules(face={"enabled": False}))
    first, _ = run(a, 4, face=lambda t: t < 1)
    assert "Stay in frame" not in first


def test_duration_setting_changes_when_alert_shows():
    a = WebcamAnalyzer()
    a.apply_settings(rules(face={"duration": 2.5}))
    first, _ = run(a, 6, face=lambda t: t < 1)
    assert first["Stay in frame"] == 3.5


def test_cooldown_keeps_alert_away_after_it_clears():
    def shown_times(cooldown):
        a = WebcamAnalyzer()
        a.apply_settings(rules(face={"cooldown": cooldown}))
        times, gone = [], True
        for i in range(10 * FPS):                     # face disappears for 1.5 s of every 3 s
            t = i / FPS
            state = a.process(frame(t, face=(t % 3) < 1.5))
            showing = "Stay in frame" in state["alerts"]
            if showing and gone:
                times.append(round(t, 1))
            gone = not showing
        return times
    assert len(shown_times(0)) == 3                 # every flicker
    assert len(shown_times(20)) == 1                # then held back by the cooldown


def test_threshold_settings_reach_the_checks():
    a = WebcamAnalyzer()
    a.apply_settings(rules(tilt={"threshold": 3}, repeat={"threshold": 90}, fast={"threshold": 5}))
    assert a.limits == {"tilt": 3.0, "look_down": 15, "fast": 5.0, "repeat": 0.9}


def test_session_start_calibration_is_immediate_and_3s():
    a = WebcamAnalyzer()
    a.process(frame(10.0))
    a.calibrate(10.0, delay=0, duration=3)
    s = a.process(frame(10.1))["calibration"]
    assert s["phase"] == "recording" and "stand still in your resting position" in s["message"]
    assert a.process(frame(12.9))["calibration"]["phase"] == "recording"
    done = a.process(frame(13.1))["calibration"]
    assert done["phase"] == "idle" and done["calibrated"]


def test_uploaded_recording_uses_its_own_opening_baseline_and_scores_framing():
    a = WebcamAnalyzer()
    # A stale live-session calibration must never influence an uploaded video.
    a.calib.baseline = {"tilt": 20, "pitch": 20, "eyes": 0.9, "rest_l": None, "rest_r": None}
    a.start_scoring(0, mode="recording")
    states = run_states(a, 0, 3.2)
    assert states[-1][1]["calibrated"]
    assert abs(a.calib.baseline["tilt"]) < 0.1

    # After calibration, an absent face and hands are body-language incidents, not omitted data.
    run_states(a, 3.2, 4, face=False, hands=0)
    assert a.incidents["face"] >= 1
    assert a.incidents["hands_missing"] >= 1


def test_manual_calibration_keeps_the_countdown():
    a = WebcamAnalyzer()
    a.process(frame(0.0))
    a.calibrate(0.0)
    assert a.process(frame(1.0))["calibration"]["phase"] == "waiting"


def run_states(a, start, secs, **kw):
    """Feeds frames from `start` for `secs`; returns (time, calibration state) per frame."""
    out = []
    for i in range(int(secs * FPS)):
        t = start + i / FPS
        f = frame(t, **{k: (v(t) if callable(v) else v) for k, v in kw.items()})
        out.append((t, a.process(f)["calibration"]))
    return out


def test_when_ready_waits_for_face_shoulders_and_hands():
    a = WebcamAnalyzer()
    a.calibrate(0.0, delay=0, duration=3, when_ready=True)
    states = run_states(a, 0.0, 3, face=False, hands=1)
    assert all(s["phase"] == "arming" for _, s in states)
    assert "show your face" in states[-1][1]["message"] and "show both hands" in states[-1][1]["message"]
    # everyone in frame and still: records after READY_HOLD_S, done 3 s later
    states = run_states(a, 3.0, 5)
    first_rec = next(t for t, s in states if s["phase"] == "recording")
    assert 3.7 <= first_rec <= 4.0
    assert states[-1][1]["phase"] == "idle" and states[-1][1]["calibrated"]


def test_when_ready_skips_hand_calibration_when_visibility_alert_is_disabled():
    a = WebcamAnalyzer()
    a.calibrate(0.0, delay=0, duration=3, when_ready=True)
    assert "show both hands" in a.process(frame(0.0, hands=0))["calibration"]["message"]

    # Restart after cancelling the session that was waiting for hands.
    a.apply_settings(rules(hands_missing={"enabled": False}))
    a.calibrate(1.0, delay=0, duration=3, when_ready=True)
    states = run_states(a, 1.0, 1, hands=0, face=False)
    assert "show your face" in states[-1][1]["message"]
    assert "show both hands" not in states[-1][1]["message"]
    states = run_states(a, 2.0, 5, hands=lambda t: 0 if t < 4 else 2)
    assert states[-1][1]["calibrated"]
    assert a.calib.baseline["rest_l"] is None and a.calib.baseline["rest_r"] is None
    assert "resting hands" not in states[-1][1]["message"]


def test_when_ready_waits_for_the_speaker_to_hold_still():
    a = WebcamAnalyzer()
    a.calibrate(0.0, delay=0, duration=3, when_ready=True)
    waving = lambda t: (0.4 + 0.08 * math.sin(2 * math.pi * 1.5 * t), 0.8)   # noqa: E731
    states = run_states(a, 0.0, 4, lwrist=waving)
    assert all(s["phase"] == "arming" for _, s in states[FPS:])            # after the first second
    assert "hold still" in states[-1][1]["message"]
    states = run_states(a, 4.0, 6)                                          # hands come to rest
    assert states[-1][1]["calibrated"]


def test_leaving_the_frame_while_recording_starts_over():
    a = WebcamAnalyzer()
    a.calibrate(0.0, delay=0, duration=3, when_ready=True)
    states = run_states(a, 0.0, 2)                                          # recording starts ~0.75 s
    assert states[-1][1]["phase"] == "recording"
    states = run_states(a, 2.0, 1, face=False)                              # walks off mid-recording
    assert states[-1][1]["phase"] == "arming" and "restarted" in states[-1][1]["message"]
    states = run_states(a, 3.0, 5)                                          # back: full 3 s again
    assert states[-1][1]["calibrated"]


def test_last_moment_movement_cannot_restart_a_completed_calibration():
    a = WebcamAnalyzer()
    a.calibrate(0.0, delay=0, duration=3, when_ready=True, request_id="camera-1")
    run_states(a, 0.0, 3.3)
    deadline = a.calib.until
    a.process(frame(deadline - 0.39, face=False))
    assert a.process(frame(deadline - 0.01, face=False))["calibration"]["phase"] == "recording"
    done = a.process(frame(deadline + 0.02, face=False))["calibration"]
    assert done["phase"] == "idle" and done["calibrated"]
    assert done["request_id"] == "camera-1"

    # A reconnect can resend the same request without discarding the baseline.
    a.calibrate(deadline + 0.03, when_ready=True, request_id="camera-1")
    assert a.process(frame(deadline + 0.04))["calibration"]["calibrated"]
    a.calibrate(deadline + 0.05, when_ready=True, request_id="camera-2")
    assert a.process(frame(deadline + 0.06))["calibration"]["phase"] == "arming"


def test_no_alerts_or_scoring_while_waiting_to_calibrate():
    a = WebcamAnalyzer()
    a.start_scoring()
    a.calibrate(0.0, delay=0, duration=3, when_ready=True)
    for i in range(3 * FPS):
        state = a.process(frame(i / FPS, face=False))
    assert state["alerts"] == [] and a.incidents.get("face", 0) == 0


def test_voice_alerts_show_while_waiting_to_calibrate():
    # calibration can wait a long time for both hands; voice / filler alerts must still show
    a = WebcamAnalyzer()
    a.calibrate(0.0, delay=0, duration=3, when_ready=True)
    state = a.process(frame(0.0, hands=0), extra_alerts=["Keep talking"])
    assert state["calibration"]["phase"] == "arming" and state["alerts"] == ["Keep talking"]

