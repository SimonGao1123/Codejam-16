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


def score_presentation(
    *,
    filler_counts: Mapping[str, int] | None = None,
    voice_incidents: Mapping[str, int] | None = None,
    body_incidents: Mapping[str, int] | None = None,
) -> dict:
    """Return a JSON-ready score out of 20, category breakdown, and ordered tips.

    An empty filler mapping means transcription found no fillers. Pass None
    when no transcript was available. The transcript's "filler?" tally is
    reported separately because those detections are unconfirmed.

    The complete rubric allocates 6 points for fillers, 3 for voice tone,
    and 11 for body language. The score uses the available points as its
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
        "score": round(MAX_SCORE * (measured_points - deducted) / measured_points, 2)
        if measured_points else None,
        "max_score": MAX_SCORE,
        "measured_points": measured_points,
        "categories": categories,
        "feedback": feedback,
    }
