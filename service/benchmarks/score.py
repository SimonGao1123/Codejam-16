"""
Score the saved runs against the references and print the tables (also results/summary.json).

  uv run --group bench python -m benchmarks.score --split test

Words are aligned per track with sclite's costs, kept filler-aware (a missed "um" next to a wrong word
must not be paired with it).
  - um/uh: real hesitations found, as any hesitation label ("uh" heard as "um" still counts; how often
    the label itself is right is reported separately). "hmm" / "mm" in the reference are ignored.
  - like / so / you know: on the ds_* tracks the reference says whether each one is a filler, so
    counting "I'd like to" is a false alarm. (Any filler-use tag counts; for "so" that includes the
    sentence-opening conjunction.)
  - alert delay (AMI tracks only, they have word times): from the end of the filler word to the pop-up,
    replaying the measured compute time of every frame against the audio clock.
"""

import argparse
import json
import re
from pathlib import Path
from statistics import median

import numpy as np

BENCH = Path(__file__).parent
FRAME_SEC = 0.03
HES = {"um", "uh", "er", "erm", "eh"}
HES_LABELS = {"um", "uh", "erm", "hmm", "filler?"}
NEUTRAL = {"hmm", "hm", "mm", "m", "ah", "mhm"}
HES_LIKE = HES | NEUTRAL | {"umm", "uhm", "uhh", "err"}
PHRASES = ["like", "so", "you know"]
BASELINE_PATTERNS = {w: rf"\b{w}\b" for w in ("um", "uh", "erm", "hmm", "like", "you know", "so")}
VOSK_PATTERNS = {w: rf"\b{w}\b" for w in ("um", "uh", "like", "you know", "so")}
BEFORE, AFTER = 0.5, 6.0         # an alert belongs to a filler from this long before its end to this after


def regex_finder(patterns):
    def find(text):
        return [(m.start(), m.end(), label) for label, p in patterns.items()
                for m in re.finditer(p, text.lower())]
    return find


def finder_for(engine):
    if engine.startswith("vosk"):
        return regex_finder(VOSK_PATTERNS)
    if engine == "app":
        from presentation_service import transcript
        if hasattr(transcript, "find_fillers"):
            return transcript.find_fillers
    return regex_finder(BASELINE_PATTERNS)


def tokens(text, find):
    """[(word, filler label or None)], "you know" as one token."""
    text = text.lower().replace("’", "'")
    spans = find(text)
    out = []
    for m in re.finditer(r"you know\b|[a-z0-9']+", text):
        label = next((lab for s, e, lab in spans if s <= m.start() and m.end() <= e), None)
        out.append((m.group(), label))
    return out


def ref_tokens(words):
    """[{w, s, e, tag}] with "you know" merged the same way."""
    out = []
    for w in words:
        if out and out[-1]["w"] == "you" and w["w"] == "know":
            out[-1] = {**out[-1], "w": "you know", "e": w["e"], "tag": out[-1].get("tag") or w.get("tag")}
        else:
            out.append(dict(w))
    return out


def align(ref, hyp):
    """Pairs (ref index or None, hyp index or None)."""
    def sub(r, h):
        if r == h:
            return 0
        r_hes, h_hes = r in HES_LIKE, h in HES_LIKE
        if r_hes and h_hes:
            return 1
        return 7 if r_hes != h_hes else 4
    n, m = len(ref), len(hyp)
    cost = np.empty((n + 1, m + 1), dtype=np.int32)
    cost[0] = np.arange(m + 1) * 3
    cost[:, 0] = np.arange(n + 1) * 3
    steps = np.arange(m + 1) * 3
    uniq = {h: i for i, h in enumerate(dict.fromkeys(hyp))}
    idx = np.array([uniq[h] for h in hyp], dtype=int)
    subs = {r: np.array([sub(r, h) for h in uniq])[idx] if len(hyp) else np.zeros(0, int) for r in set(ref)}
    for i in range(1, n + 1):
        row = np.empty(m + 1, dtype=np.int64)
        row[0] = i * 3
        row[1:] = np.minimum(cost[i - 1, :-1] + subs[ref[i - 1]], cost[i - 1, 1:] + 3)
        cost[i] = np.minimum.accumulate(row - steps) + steps      # insertions
    pairs, i, j = [], n, m
    while i or j:
        if i and j and cost[i, j] == cost[i - 1, j - 1] + subs[ref[i - 1]][j - 1]:
            i, j = i - 1, j - 1
            pairs.append((i, j))
        elif i and cost[i, j] == cost[i - 1, j] + 3:
            i -= 1
            pairs.append((i, None))
        else:
            j -= 1
            pairs.append((None, j))
    return pairs[::-1]


def prf(tp, fp, fn):
    p, r = tp / (tp + fp) if tp + fp else 0.0, tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def finish_times(track):
    """When each frame's work is done on the audio clock, if audio arrives in real time."""
    t, out = 0.0, []
    for i, ms in enumerate(track["frame_ms"]):
        t = max(t, (i + 1) * FRAME_SEC) + ms / 1000
        out.append(t)
    out.append(max(t, len(out) * FRAME_SEC) + track["flush_ms"] / 1000)
    return out


def match_times(found, refs):
    """Pair sorted found times with sorted reference word ends, in order -> list of delays."""
    delays, used = [], set()
    for t in sorted(found):
        for i, (s, e) in enumerate(refs):
            if i not in used and e - BEFORE <= t <= e + AFTER:
                used.add(i)
                delays.append(t - e)
                break
    return delays


def score(run, split):
    import jiwer
    from whisper_normalizer.english import EnglishTextNormalizer
    norm = EnglishTextNormalizer()
    find = finder_for(run["engine"])
    counts = {}

    def add(key, kind, n=1):
        counts.setdefault(key, {"tp": 0, "fp": 0, "fn": 0})[kind] += n

    wer = {"ami": [[], []], "ds": [[], []]}
    delays, marker_err, alerts, label_ok, label_n, totals_err = [], [], 0, 0, 0, []
    n_ami_hes = audio = busy = cpu = 0
    frame_ms, backlog = [], 0
    for name, track in run["tracks"].items():
        ref = ref_tokens(json.loads((BENCH / "data" / split / f"{name}.json").read_text())["words"])
        kind = name.split("_")[0]
        hyp_text = " ".join(track["sentences"]).replace("[um?]", "um").replace("[", "").replace("]", "")
        hyp = tokens(hyp_text, find)
        wer[kind][0].append(norm(" ".join(w["w"] for w in ref)))
        wer[kind][1].append(norm(hyp_text))

        for ri, hi in align([w["w"] for w in ref], [w for w, _ in hyp]):
            r = ref[ri] if ri is not None else {"w": None}
            w, label = hyp[hi] if hi is not None else (None, None)
            if w in HES_LIKE and label is None and run["engine"].startswith("vosk"):
                label = None
            is_hes, found_hes = r["w"] in HES, label in HES_LABELS
            if is_hes:
                for k in (f"hes_{kind}", "hes"):
                    add(k, "tp" if found_hes else "fn")
                if found_hes:
                    label_n += 1
                    label_ok += label == r["w"]
            elif found_hes and r["w"] not in NEUTRAL:
                for k in (f"hes_{kind}", "hes"):
                    add(k, "fp")
            # words: was it heard (any use), and on ds was it counted only when it's a filler
            if r["w"] in PHRASES:
                add("heard_" + r["w"], "tp" if w == r["w"] else "fn")
            if kind == "ds":
                filler_use = r["w"] in PHRASES and r.get("tag") is not None
                counted = label in PHRASES
                for k in {r["w"]} if filler_use else set():
                    add(k, "tp" if counted else "fn")
                if counted and not filler_use:
                    add(label, "fp")
                true = filler_use or is_hes
                got = counted or found_hes
                if true or (got and r["w"] not in NEUTRAL):
                    add("all_ds", "tp" if true and got else "fn" if true else "fp")

        done = finish_times(track)
        if kind == "ami":
            hes = [(w["s"], w["e"]) for w in ref if w["w"] in HES]
            n_ami_hes += len(hes)
            found = [done[i] for i, k, v in track["events"] if k == "filler" and v in HES_LABELS]
            alerts += len(found)
            delays += match_times(found, hes)
            # the marker on the playback timeline, against the closest real hesitation
            marks = [when for when, w in track["filler_checkpoints"] if w in HES_LABELS]
            if hes:
                ends = np.array([s for s, _ in hes])
                marker_err += [float(np.abs(ends - m).min()) for m in marks]
        shown = sum(1 for _, k, _ in track["events"] if k == "filler")
        totals_err.append((shown, sum(track["totals"].values())))
        audio += track["seconds"]
        busy += sum(track["frame_ms"]) / 1000 + track["flush_ms"] / 1000
        cpu += track["cpu_s"]
        frame_ms += track["frame_ms"]
        backlog = max(backlog, max(d - (i + 1) * FRAME_SEC for i, d in enumerate(done[:-1])))

    q = lambda xs, p: float(np.percentile(xs, p)) if len(xs) else None      # noqa: E731
    return {
        "engine": run["engine"], "audio_min": audio / 60,
        "wer": {k: jiwer.wer(*v) for k, v in wer.items() if v[0]},
        "fillers": {k: prf(**v) for k, v in counts.items()},
        "label_accuracy": label_ok / label_n if label_n else None,
        "alert_delay_p50": q(delays, 50), "alert_delay_p90": q(delays, 90),
        "alerted_share": len(delays) / n_ami_hes if n_ami_hes else None,
        "alerts_per_real": alerts / n_ami_hes if n_ami_hes else None,
        "marker_error_p50": q(marker_err, 50), "marker_error_p90": q(marker_err, 90),
        "shown_vs_final": sum(s for s, _ in totals_err) / max(1, sum(f for _, f in totals_err)),
        "rtf": busy / audio, "cpu_per_audio_s": cpu / audio,
        "frame_ms_mean": float(np.mean(frame_ms)), "frame_ms_p99": q(frame_ms, 99), "frame_ms_max": max(frame_ms),
        "worst_backlog_s": backlog,
        "load_s": run["load_s"], "rss_peak_mb": run["rss_peak_mb"], "rss_loaded_mb": run["rss_loaded_mb"],
    }


def table(rows, cols):
    print("| " + " | ".join(c for c, _ in cols) + " |\n|" + "---|" * len(cols))
    for r in rows:
        print("| " + " | ".join(f(r) for _, f in cols) + " |")
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test")
    ap.add_argument("engines", nargs="*")
    args = ap.parse_args()
    rows = []
    for path in sorted((BENCH / "results").glob(f"*_{args.split}.json")):
        tag = path.stem.rsplit("_", 1)[0]
        if tag == "summary" or (args.engines and tag not in args.engines):
            continue
        rows.append({**score(json.loads(path.read_text()), args.split), "tag": tag})
    pct = lambda x: "-" if x is None else f"{x * 100:.1f}%"                 # noqa: E731
    num = lambda x, d=2: "-" if x is None else f"{x:.{d}f}"                 # noqa: E731
    F = lambda r, k, f: pct(r["fillers"].get(k, {}).get(f))                 # noqa: E731
    table(rows, [("um/uh", lambda r: r["tag"]),
                 ("WER ami", lambda r: pct(r["wer"].get("ami"))), ("WER ds", lambda r: pct(r["wer"].get("ds"))),
                 ("P", lambda r: F(r, "hes", "precision")), ("R", lambda r: F(r, "hes", "recall")),
                 ("F1", lambda r: F(r, "hes", "f1")), ("F1 ami", lambda r: F(r, "hes_ami", "f1")),
                 ("F1 ds", lambda r: F(r, "hes_ds", "f1")), ("right label", lambda r: pct(r["label_accuracy"])),
                 ("n", lambda r: str(sum(r["fillers"].get("hes", {}).get(k, 0) for k in ("tp", "fn"))))])
    table(rows, [("ds fillers (P / R / F1)", lambda r: r["tag"])] +
          [(k, lambda r, k=k: f"{F(r, k, 'precision')} / {F(r, k, 'recall')} / {F(r, k, 'f1')}")
           for k in PHRASES + ["all_ds"]] +
          [("heard " + k, lambda r, k=k: F(r, "heard_" + k, "recall")) for k in PHRASES])
    table(rows, [("speed", lambda r: r["tag"]), ("alert p50 s", lambda r: num(r["alert_delay_p50"])),
                 ("p90 s", lambda r: num(r["alert_delay_p90"])), ("alerted", lambda r: pct(r["alerted_share"])),
                 ("marker err p50 s", lambda r: num(r["marker_error_p50"])),
                 ("p90", lambda r: num(r["marker_error_p90"])),
                 ("RTF", lambda r: num(r["rtf"], 3)), ("CPU s / audio s", lambda r: num(r["cpu_per_audio_s"])),
                 ("feed p99 ms", lambda r: num(r["frame_ms_p99"], 0)), ("backlog s", lambda r: num(r["worst_backlog_s"])),
                 ("load s", lambda r: num(r["load_s"], 1)), ("RSS MB", lambda r: num(r["rss_peak_mb"], 0)),
                 ("alerts shown / counted", lambda r: num(r["shown_vs_final"]))])
    (BENCH / "results" / f"summary_{args.split}.json").write_text(json.dumps(rows, indent=1))
