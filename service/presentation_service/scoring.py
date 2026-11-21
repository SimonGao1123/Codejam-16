"""Calculate a post-practice delivery score from session-level analyzer totals.

Pass the final transcript's filler counts and counts of distinct voice and
webcam incidents during the recorded session. The incident keys match the
existing live rule IDs. Count a sustained webcam condition once, even if it
appears in many frames. Use 0 for rules that were measured and never triggered,
and omit rules that could not be evaluated (e.g. shoulders without calibration).
"""

from collections.abc import Mapping

FILLER_MAX_POINTS = 6
LIGHT_FILLER_COUNT = 2
LIGHT_FILLER_DEDUCTION = 0.25
REPEATED_FILLER_DEDUCTION = 0.75

# Rule ID: (deduction per incident, maximum deduction, feedback)
VOICE_RULES = {
    "volume": (0.5, 1, "Keep your volume steady and soften sudden loud peaks."),
    "pitch": (0.5, 1, "Vary your tone naturally and smooth out sudden jumps in pitch."),
    "quiet": (0.5, 1, "Project a little more during quiet passages."),
}
BODY_RULES = {
    "look_down": (1.25, 4, "Bring your eyes back to the calibrated camera position after checking notes."),
    "face": (0.5, 1.5, "Stay in frame and face the camera while speaking."),
    "tilt": (0.75, 2, "Keep your shoulders level with your calibrated posture."),
    "repeat": (0.25, 1, "Vary your gestures and avoid repeating the same motion in quick succession."),
    "still": (0.5, 1.5, "Use an occasional purposeful gesture to emphasize a point."),
    "fast": (0.25, 1, "Slow down quick hand movements so each gesture has a clear purpose."),
}
MAX_SCORE = FILLER_MAX_POINTS + sum(rule[1] for rule in (*VOICE_RULES.values(), *BODY_RULES.values()))
PACE_MAX_POINTS = 4
# Presentation guidance varies with context.  A 140 WPM centre and a 40 WPM
# comfortable band are sensible defaults; the centre is configurable in the UI.
PACE_COMFORT_BAND_WPM = 40
PACE_ZERO_CREDIT_DISTANCE_WPM = 60
PACE_CONSISTENT_CV = 0.15
PACE_INCONSISTENT_CV = 0.45
VOLUME_MAX_POINTS = 4            # projection 2, steady volume 1, long pauses 1
PROJECTION_FULL_DB = 25          # typical speech this far above the room's noise = full credit
PROJECTION_ZERO_DB = 10          # ...this close (barely above "not speaking") = none
VOLUME_STEADY_DB = 3             # spread (standard deviation) of the 10 s speech levels: full credit
VOLUME_UNSTEADY_DB = 9           # ...no credit
LONG_PAUSE_RULE = (0.5, 1, "Keep pauses to a few seconds so you don't lose the room.")


def _count(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Scoring counts must be nonnegative integers")
    return value


def _category(checks: dict) -> dict:
    maximum = sum(check["max_points"] for check in checks.values())
    deducted = sum(check["deduction"] for check in checks.values())
    return {
        "points": maximum - deducted if maximum else None,
        "max_points": maximum,
        "deduction": deducted,
        "checks": checks,
    }


def _incident_category(counts: Mapping[str, int] | None, rules: dict) -> dict:
    checks = {}
    for rule_id, (rate, maximum, tip) in rules.items():
        if counts is None or rule_id not in counts:
            continue
        count = _count(counts[rule_id])
        checks[rule_id] = {
            "count": count,
            "max_points": maximum,
            "deduction": min(count * rate, maximum),
            "feedback": tip if count else None,
        }
    return _category(checks)


def _pace_category(pace: Mapping[str, object] | None, target_wpm: float) -> dict:
    """Score a moderate average pace (2 pts) and a steady pace (2 pts)."""
    if pace is None:
        return _category({})
    average = pace.get("average_wpm")
    samples = pace.get("segment_wpms")
    if not isinstance(average, (int, float)) or average <= 0:
        return _category({})
    target = min(200, max(80, float(target_wpm)))
    distance = abs(float(average) - target)
    beyond_band = max(0, distance - PACE_COMFORT_BAND_WPM)
    moderate_deduction = 2 * min(1, beyond_band / (PACE_ZERO_CREDIT_DISTANCE_WPM - PACE_COMFORT_BAND_WPM))
    low, high = target - PACE_COMFORT_BAND_WPM, target + PACE_COMFORT_BAND_WPM
    checks = {
        "moderate_pace": {
            "count": 0,
            "max_points": 2,
            "deduction": round(moderate_deduction, 2),
            "feedback": (f"Aim for about {target:g} WPM ({low:g}–{high:g} WPM is the target range)."
                         if moderate_deduction else None),
            "average_wpm": round(float(average), 1),
            "target_wpm": target,
        }
    }
    valid_samples = [float(rate) for rate in samples] if isinstance(samples, list) and all(
        isinstance(rate, (int, float)) and rate > 0 for rate in samples
    ) else []
    if len(valid_samples) >= 2:
        mean = sum(valid_samples) / len(valid_samples)
        cv = (sum((rate - mean) ** 2 for rate in valid_samples) / len(valid_samples)) ** 0.5 / mean
        inconsistent = max(0, cv - PACE_CONSISTENT_CV)
        consistency_deduction = 2 * min(1, inconsistent / (PACE_INCONSISTENT_CV - PACE_CONSISTENT_CV))
        checks["consistent_pace"] = {
            "count": len(valid_samples),
            "max_points": 2,
            "deduction": round(consistency_deduction, 2),
            "feedback": "Keep a steadier pace from one part of the presentation to the next."
                        if consistency_deduction else None,
        }
    return _category(checks)


def _volume_category(volume: Mapping[str, object] | None, voice_incidents: Mapping[str, int] | None) -> dict:
    """Projection (speech level above the room's noise, 2 pts), steady volume across the session
    (1 pt) and long pauses (1 pt)."""
    checks = {}
    if volume is not None:
        speech, room, segments = volume.get("speech_dbfs"), volume.get("room_dbfs"), volume.get("segment_dbfs") or []
        if isinstance(speech, (int, float)) and isinstance(room, (int, float)):
            margin = float(speech) - float(room)
            short = (PROJECTION_FULL_DB - margin) / (PROJECTION_FULL_DB - PROJECTION_ZERO_DB)
            deduction = round(2 * min(1, max(0, short)), 2)
            checks["projection"] = {
                "count": 0,
                "max_points": 2,
                "deduction": deduction,
                "feedback": (f"Project more: your voice was {margin:.0f} dB above the room's background "
                             f"noise; aim for {PROJECTION_FULL_DB} dB or more." if deduction else None),
                "margin_db": round(margin, 1),
            }
        if len(segments) >= 2:
            mean = sum(segments) / len(segments)
            spread = (sum((s - mean) ** 2 for s in segments) / len(segments)) ** 0.5
            deduction = round(min(1, max(0, (spread - VOLUME_STEADY_DB) / (VOLUME_UNSTEADY_DB - VOLUME_STEADY_DB))), 2)
            trailing_off = segments[-1] == min(segments) and segments[0] - segments[-1] > VOLUME_STEADY_DB
            checks["steady_volume"] = {
                "count": len(segments),
                "max_points": 1,
                "deduction": deduction,
                "feedback": None if not deduction else (
                    "You got quieter as you went on; keep your volume up to the end." if trailing_off
                    else "Keep your volume even from one part of the presentation to the next."),
            }
    if voice_incidents is not None and "pause" in voice_incidents:
        rate, maximum, tip = LONG_PAUSE_RULE
        count = _count(voice_incidents["pause"])
        checks["long_pause"] = {
            "count": count,
            "max_points": maximum,
            "deduction": min(count * rate, maximum),
            "feedback": tip if count else None,
        }
    return _category(checks)


def score_presentation(
    *,
    filler_counts: Mapping[str, int] | None = None,
    voice_incidents: Mapping[str, int] | None = None,
    body_incidents: Mapping[str, int] | None = None,
    pace: Mapping[str, object] | None = None,
    pace_target_wpm: float = 140,
    volume: Mapping[str, object] | None = None,
) -> dict:
    """Return a JSON-ready score out of 20, category breakdown, and ordered tips.

    An empty filler mapping means transcription found no fillers. Pass None
    when no transcript was available. The transcript's "filler?" tally is
    reported separately because those detections are unconfirmed.

    The complete rubric allocates 6 points for fillers, 3 for voice tone, 4 for volume
    (projection, steadiness, long pauses; `volume` is AudioAnalyzer.volume_summary()),
    4 for speaking pace, and 11 for body language. The score uses the available points as its
    denominator. measured_points shows how much of the rubric was evaluated.
    With no measured data, score is None.
    """
    filler_checks = {}
    if filler_counts is not None:
        count = sum(_count(value) for word, value in filler_counts.items() if word != "filler?")
        light = min(count, LIGHT_FILLER_COUNT)
        deduction = light * LIGHT_FILLER_DEDUCTION + (count - light) * REPEATED_FILLER_DEDUCTION
        filler_checks["filler"] = {
            "count": count,
            "suspected_count": _count(filler_counts.get("filler?", 0)),
            "max_points": FILLER_MAX_POINTS,
            "deduction": min(deduction, FILLER_MAX_POINTS),
            "feedback": "Try a short pause instead of filling the silence." if count else None,
        }

    categories = {
        "filler": _category(filler_checks),
        "voice_tone": _incident_category(voice_incidents, VOICE_RULES),
        "volume": _volume_category(volume, voice_incidents),
        "speaking_pace": _pace_category(pace, pace_target_wpm),
        "body_language": _incident_category(body_incidents, BODY_RULES),
    }
    measured_points = sum(category["max_points"] for category in categories.values())
    deducted = sum(category["deduction"] for category in categories.values())
    feedback = [
        {"category": category_name, "rule": rule_id, "message": check["feedback"],
         "deduction": check["deduction"]}
        for category_name, category in categories.items()
        for rule_id, check in category["checks"].items()
        if check["feedback"]
    ]
    feedback.sort(key=lambda tip: tip["deduction"], reverse=True)
    return {
        "score": round((MAX_SCORE + PACE_MAX_POINTS + VOLUME_MAX_POINTS) * (measured_points - deducted) / measured_points, 2)
        if measured_points else None,
        "max_score": MAX_SCORE + PACE_MAX_POINTS + VOLUME_MAX_POINTS,
        "measured_points": measured_points,
        "categories": categories,
        "feedback": feedback,
    }
