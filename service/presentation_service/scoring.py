"""Calculate a post-practice delivery score from session-level analyzer totals.

Pass the final transcript's filler counts and counts of distinct voice and
webcam incidents during the recorded session. The incident keys match the
existing live rule IDs. Count a sustained webcam condition once, even if it
appears in many frames. Use 0 for rules that were measured and never triggered,
and omit rules that could not be evaluated (e.g. shoulders without calibration).

Filler scoring is rate-based. Counts are converted to incidents per
minute, then compared to two thresholds:

  * at or below the GOOD rate  -> no deduction (normal, natural speech)
  * at or above the BAD rate   -> the rule's full maximum deduction
  * in between                 -> the deduction rises linearly

Voice and most body-language checks use sustained incident counts directly.
Hand visibility deductions are based on incidents per minute.
"""

from collections.abc import Collection, Mapping

# Presentations shorter than this are scored as if they were this long, so one
# slip in a very short clip can't dominate the score.
MIN_SCORING_MINUTES = 1.0
# Used when no length is known at all (neither recorded nor expected).
FALLBACK_MINUTES = 5.0

FILLER_MAX_POINTS = 6
# (good rate, bad rate) in fillers per minute
FILLER_RATES = (2.0, 8.0)

# Rule ID: (maximum points, fraction lost per sustained incident, feedback)
VOICE_RULES = {
    "volume": (2, 0.75, "Keep your volume steady and soften sudden loud peaks."),
    "pitch": (2, 1.0, "Smooth out sudden rises and drops in pitch."),
    "quiet": (2, 0.75, "Project a little more during quiet passages."),
}
# Rule ID: (maximum points, fraction lost per sustained incident, feedback)
BODY_RULES = {
    "look_down": (1.5, 0.75, "Bring your eyes back to the calibrated camera position after checking notes."),
    "face": (2.5, 1.0, "Keep your full face and both shoulders visible throughout the presentation."),
    "tilt": (1, 0.75, "Keep your shoulders level with your calibrated posture."),
    "repeat": (1.5, 0.75, "Vary your gestures and avoid repeating the same motion in quick succession."),
    "still": (2, 0.75, "Use an occasional purposeful gesture to emphasize a point."),
    "fast": (1.5, 0.75, "Slow down quick hand movements so each gesture has a clear purpose."),
}
HAND_CHECKS = ("still", "repeat", "fast")
PACE_MAX_POINTS = 4
# Presentation guidance varies with context.  A 140 WPM centre and a 40 WPM
# comfortable band are sensible defaults; the centre is configurable in the UI.
PACE_COMFORT_BAND_WPM = 40
PACE_ZERO_CREDIT_DISTANCE_WPM = 60
PACE_CONSISTENT_CV = 0.15
PACE_INCONSISTENT_CV = 0.45

# Script accuracy: script_match is 0-1 (1 = delivered exactly the script).
# Full credit at SCRIPT_GOOD_MATCH or above, no credit at SCRIPT_BAD_MATCH or below.
SCRIPT_MAX_POINTS = 6
SCRIPT_GOOD_MATCH = 0.85
SCRIPT_BAD_MATCH = 0.5

# Script order: each "Skipped ahead" / "Went back" event costs a little, capped
SCRIPT_ORDER_MAX_POINTS = 2
SCRIPT_ORDER_LOSS_PER_EVENT = 0.5

# Time limit (only when the user set one): no deduction up to TIME_GRACE over the limit, the full
# TIME_MAX_POINTS at TIME_FULL_OVER or more over, linear in between. Finishing early is fine.
TIME_MAX_POINTS = 3
TIME_GRACE = 0.05
TIME_FULL_OVER = 0.30

SCORE_OUT_OF = 100

# Most checks share the live alert's ID. These checks use derived measurements.
CHECK_ALERTS = {
    "moderate_pace": ("pace_avg",),
    "consistent_pace": ("pace",),
    "script_match": ("off_script", "out_of_order", "stuck"),
    "script_order": ("out_of_order",),
}


def _count(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Scoring counts must be nonnegative integers")
    return value


def _minutes(duration_seconds: float | None) -> float | None:
    """Presentation length in minutes, or None if unknown or invalid."""
    if (
        not isinstance(duration_seconds, (int, float))
        or isinstance(duration_seconds, bool)
        or duration_seconds <= 0
    ):
        return None
    return duration_seconds / 60


def _rate_deduction(rate: float, good: float, bad: float, maximum: float) -> float:
    """0 at or below `good`, `maximum` at or above `bad`, linear in between."""
    share = (rate - good) / (bad - good)
    return maximum * min(1.0, max(0.0, share))


def _category(checks: dict) -> dict:
    maximum = sum(check["max_points"] for check in checks.values())
    deducted = sum(check["deduction"] for check in checks.values())
    return {
        "points": maximum - deducted if maximum else None,
        "max_points": maximum,
        "deduction": deducted,
        "checks": checks,
    }


def _sustained_category(counts: Mapping[str, int] | None, rules: dict) -> dict:
    checks = {}
    for rule_id, (maximum, loss_per_incident, tip) in rules.items():
        if counts is None or rule_id not in counts:
            continue
        count = _count(counts[rule_id])
        checks[rule_id] = {
            "count": count,
            "max_points": maximum,
            "deduction": maximum * min(1, count * loss_per_incident),
            "feedback": tip if count else None,
        }
    return _category(checks)


def _pace_category(pace: Mapping[str, object] | None, target_wpm: float) -> dict:
    """Score a moderate average pace (2 pts) and a steady pace (2 pts).

    Pace is already a rate (words per minute), so it needs no length scaling.
    """
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


def _clock(minutes: float) -> str:
    seconds = round(minutes * 60)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _time_category(recorded_minutes: float | None, limit_minutes: float | None) -> dict:
    """Going over the user's time limit (3 pts): none lost up to TIME_GRACE over, all at
    TIME_FULL_OVER or more. Not measured without a limit or a recorded length."""
    if not recorded_minutes or not isinstance(limit_minutes, (int, float)) or limit_minutes <= 0:
        return _category({})
    over = recorded_minutes / limit_minutes - 1
    deduction = _rate_deduction(over, TIME_GRACE, TIME_FULL_OVER, TIME_MAX_POINTS)
    return _category({
        "over_time": {
            "count": 0,
            "recorded_minutes": round(recorded_minutes, 2),
            "limit_minutes": round(float(limit_minutes), 2),
            "over_percent": round(max(0.0, over) * 100, 1),
            "max_points": TIME_MAX_POINTS,
            "deduction": round(deduction, 3),
            "feedback": (f"You went {max(0.0, over) * 100:.0f}% over your {_clock(limit_minutes)} "
                         f"limit ({_clock(recorded_minutes)}). Trim content or pick up the pace.")
                        if deduction > 0 else None,
        }
    })


def _script_category(script_match: float | None, order_events: int | None = None) -> dict:
    """Score how closely the delivery followed the script (script_match is 0-1, 6 pts) and how
    often it skipped ahead or went back (order_events, 2 pts)."""
    if not isinstance(script_match, (int, float)) or isinstance(script_match, bool):
        return _category({})
    match = float(script_match)
    if match != match:  # NaN
        return _category({})
    match = min(1.0, max(0.0, match))
    deduction = _rate_deduction(
        SCRIPT_GOOD_MATCH - match, 0.0, SCRIPT_GOOD_MATCH - SCRIPT_BAD_MATCH, SCRIPT_MAX_POINTS
    )
    checks = {
        "script_match": {
            "count": 0,
            "match": round(match, 3),
            "good_match": SCRIPT_GOOD_MATCH,
            "bad_match": SCRIPT_BAD_MATCH,
            "max_points": SCRIPT_MAX_POINTS,
            "deduction": round(deduction, 3),
            "feedback": "Stay closer to your script; parts were skipped or reworded too much."
                        if deduction > 0 else None,
        }
    }
    if order_events is not None:
        count = _count(order_events)
        order_deduction = min(SCRIPT_ORDER_MAX_POINTS, count * SCRIPT_ORDER_LOSS_PER_EVENT)
        checks["script_order"] = {
            "count": count,
            "max_points": SCRIPT_ORDER_MAX_POINTS,
            "deduction": order_deduction,
            "feedback": (f"Follow your script in order: you skipped ahead or went back "
                         f"{count} time{'s' if count != 1 else ''}.") if count else None,
        }
    return _category(checks)


def score_presentation(
    *,
    filler_counts: Mapping[str, int] | None = None,
    voice_incidents: Mapping[str, int] | None = None,
    body_incidents: Mapping[str, int] | None = None,
    pace: Mapping[str, object] | None = None,
    pace_target_wpm: float = 140,
    duration_seconds: float | None = None,
    expected_minutes: float | None = None,
    script_match: float | None = None,
    script_order_events: int | None = None,
    volume: Mapping[str, object] | None = None,
    disabled_alerts: Collection[str] = (),
) -> dict:
    """Return a JSON-ready score out of 100, category breakdown, and ordered tips.

    volume is the summary from AudioAnalyzer.volume_summary(). It is accepted so
    the service can pass it, but it is NOT used in the score yet (volume is still
    scored from the "volume" and "quiet" incident counts).

    script_match is how closely the delivery followed the user's script, from
    0 to 1. Pass None when no script was provided; the script category is then
    left out of the score entirely. script_order_events: how many times the speaker
    skipped ahead or went back in the script (None = not measured).

    Time limit: with expected_minutes (the user's time limit) and a recorded
    duration_seconds, going more than TIME_GRACE over the limit costs up to
    TIME_MAX_POINTS (category "time_limit").

    An empty filler mapping means transcription found no fillers. Pass None
    when no transcript was available. The transcript's "filler?" tally is
    reported separately because those detections are unconfirmed.

    Length: duration_seconds (the recorded length) is used when available;
    otherwise expected_minutes (what the user said to expect); otherwise
    FALLBACK_MINUTES is assumed. Lengths under MIN_SCORING_MINUTES are scored
    as MIN_SCORING_MINUTES. The result's scoring_minutes shows the length used.

    The complete rubric allocates 6 points for fillers, 6 for voice tone,
    4 for speaking pace, 10 for body language, 8 for script accuracy (6 coverage, 2 order)
    and 3 for the time limit. The score
    is the share of the available points kept, scaled to 100. measured_points shows
    how much of the rubric was evaluated. Checks tied to disabled alerts are excluded.
    A second sustained framing failure can deduct another 2.5 measured body-language points.
    With no measured data, score is None.
    """
    minutes = _minutes(duration_seconds)
    duration_source = "recorded" if minutes else None
    if minutes is None and isinstance(expected_minutes, (int, float)) and not isinstance(expected_minutes, bool):
        minutes = _minutes(expected_minutes * 60)
        duration_source = "expected" if minutes else None
    length_known = minutes is not None
    scoring_minutes = max(minutes, MIN_SCORING_MINUTES) if length_known else FALLBACK_MINUTES

    filler_checks = {}
    if filler_counts is not None:
        count = sum(_count(value) for word, value in filler_counts.items() if word != "filler?")
        good, bad = FILLER_RATES
        rate = count / scoring_minutes
        filler_checks["filler"] = {
            "count": count,
            "per_minute": round(rate, 2) if length_known else None,
            "good_rate": good,
            "bad_rate": bad,
            "suspected_count": _count(filler_counts.get("filler?", 0)),
            "max_points": FILLER_MAX_POINTS,
            "deduction": round(_rate_deduction(rate, good, bad, FILLER_MAX_POINTS), 3),
            "feedback": "Try a short pause instead of filling the silence." if count else None,
        }

    missing_hands = (
        _count(body_incidents.get("hands_missing", 0))
        if body_incidents and "hands_missing" not in disabled_alerts
        else 0
    )
    if missing_hands:
        # Missing hands prevented gesture measurements, but enabled hand checks still count.
        body_incidents = {**dict.fromkeys(HAND_CHECKS, 0), **body_incidents}

    categories = {
        "filler": _category(filler_checks),
        "voice_tone": _sustained_category(voice_incidents, VOICE_RULES),
        "speaking_pace": _pace_category(pace, pace_target_wpm),
        "body_language": _sustained_category(body_incidents, BODY_RULES),
        "script_accuracy": _script_category(script_match, script_order_events),
        "time_limit": _time_category(
            minutes if duration_source == "recorded" else None, expected_minutes
        ),
    }
    if disabled_alerts:
        categories = {
            name: _category({
                check_id: check
                for check_id, check in category["checks"].items()
                if not any(alert in disabled_alerts for alert in CHECK_ALERTS.get(check_id, (check_id,)))
            })
            for name, category in categories.items()
        }
    body = categories["body_language"]
    missing_hands_deduction = 0
    if missing_hands:
        available_hand_points = sum(
            check["max_points"] - check["deduction"]
            for key, check in body["checks"].items() if key in HAND_CHECKS
        )
        hand_scoring_minutes = max(MIN_SCORING_MINUTES, minutes or MIN_SCORING_MINUTES)
        missing_hands_deduction = min(
            round(0.75 * missing_hands / hand_scoring_minutes, 3), available_hand_points
        )
        body["points"] -= missing_hands_deduction
        body["deduction"] += missing_hands_deduction
    framing = body["checks"].get("face")
    second_framing_deduction = 0
    if framing and framing["count"] >= 2:
        second_framing_deduction = min(body["points"], framing["max_points"])
        body["points"] -= second_framing_deduction
        body["deduction"] += second_framing_deduction

    measured_points = sum(category["max_points"] for category in categories.values())
    deducted = sum(category["deduction"] for category in categories.values())
    feedback = [
        {"category": category_name, "rule": rule_id, "message": check["feedback"],
         "deduction": check["deduction"] + (second_framing_deduction if category_name == "body_language" and rule_id == "face" else 0)}
        for category_name, category in categories.items()
        for rule_id, check in category["checks"].items()
        if check["feedback"] and check["deduction"] > 0
    ]
    if missing_hands_deduction:
        feedback.append({
            "category": "body_language", "rule": "hands_missing",
            "message": "Keep both hands visible. Frequent losses of hand visibility lower your score.",
            "deduction": missing_hands_deduction,
        })
    feedback.sort(key=lambda tip: tip["deduction"], reverse=True)
    return {
        "score": round(SCORE_OUT_OF * (measured_points - deducted) / measured_points, 1)
        if measured_points else None,
        "max_score": SCORE_OUT_OF,
        "measured_points": measured_points,
        "duration_minutes": round(minutes, 2) if length_known else None,
        "duration_source": duration_source,
        "scoring_minutes": round(scoring_minutes, 2),
        "categories": categories,
        "feedback": feedback,
    }
