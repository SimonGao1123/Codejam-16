"""
Script tracking: speed and accuracy, replaying the transcripts saved by benchmarks.run (no audio, so it
measures only the tracker). DisfluencySpeech tracks are read as if their fluent words were the script
(one utterance per sentence), so the true position at any moment is known. Four situations:

  - reading:    the script as spoken -> is the position right, how late is it, false alerts
  - skipping:   1 or 3 sentences that are never said after every 10 -> are they reported skipped
  - going back: the script's blocks of 10 are swapped -> is there a "went back" alert
  - off script: someone else's script -> is it noticed

  uv run --group bench python -m benchmarks.script [--service other/checkout/service] [--split test]
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

BENCH = Path(__file__).parent
FRAME_SEC = 0.03
OFF = ("off topic", "ad-libbing (on topic, not a script line)")


def load(split, run):
    """[(sentences said, [(time, transcript so far)], [(start, end) of each sentence])] per track."""
    tracks = json.loads((BENCH / "results" / f"{run}_{split}.json").read_text())["tracks"]
    out = []
    for name, track in tracks.items():
        if not name.startswith("ds"):
            continue
        utterances = {}
        for w in json.loads((BENCH / "data" / split / f"{name}.json").read_text())["words"]:
            utterances.setdefault((w["s"], w["e"]), []).append(w)
        sentences = [" ".join(w["w"] for w in ws if w.get("tag") != "F").capitalize() + "."
                     for ws in utterances.values()]
        done, timeline, partial = [], [], ""
        for frame, kind, value in track["events"]:
            if kind == "partial":
                partial = value
            elif kind == "sentence":
                done.append(value.replace("[", "").replace("]", ""))
            else:
                continue
            timeline.append(((frame + 1) * FRAME_SEC, " ".join(done + [partial]).strip()))
        out.append((sentences, timeline, list(utterances)))
    return out


def replay(sc, embed, script, said, timeline, spans, every):
    """Drives the tracker like AudioAnalyzer.check_script does: on new text, at most every `every` s.
    said: for each spoken sentence, its index in `script` (None = not in it)."""
    chunks = sc.chunk_script(" ".join(script))
    tracker = sc.ScriptTracker(chunks, embed)
    owner, i = [], 0                              # chunk of each script sentence (short ones are merged)
    for s in script:
        while i < len(chunks) - 1 and s not in chunks[i]:
            i += 1
        owner.append(i)
    truth = [(s, owner[k]) for (s, _), k in zip(spans, said) if k is not None]
    starts = {}
    for s, c in truth:
        starts.setdefault(c, s)
    last, last_text, spent, log, events, reached = -1e9, "", [], [], [], {}
    for t, text in timeline:
        if text == last_text or t - last < every:
            continue
        last, last_text = t, text
        begin = time.perf_counter()
        m = tracker.update(text, t)
        spent.append((time.perf_counter() - begin) * 1000)
        if m is None:
            continue
        true = max((i for i, (s, c) in enumerate(truth) if s <= t - 0.3), default=0)
        log.append((t, truth[true][1] if truth else 0, m.position, m.best, m.status))
        events += [e["kind"] for e in m.events]
        for c in starts:
            if m.position == c:
                reached.setdefault(c, t)
    return {"spent": spent, "off": [abs(x[2] - x[1]) for x in log], "events": events, "log": log,
            "lags": [reached[c] - starts[c] for c in starts if c in reached and starts[c] > starts[min(starts)]],
            "skipped": {owner.index(c) for c in tracker.skipped}, "parts": len(chunks)}


def reading(sc, embed, data, every):
    return [replay(sc, embed, s, range(len(s)), t, sp, every) for s, t, sp in data]


def skipping(sc, embed, data, every, size=3):
    """-> (inserted sentences reported skipped, inserted, spoken reported skipped, alerts, gaps)."""
    hit = total = wrong = alerts = gaps = 0
    for k, (s, t, sp) in enumerate(data):
        other = [x for x in next(d[0] for d in data[k + 1:] + data[:k] if d[0] != s) if len(x.split()) > 8]
        script, said, extra = [], [], set()
        for i, sentence in enumerate(s):
            if i and i % 10 == 0:
                for _ in range(size):
                    extra.add(len(script))
                    script.append(other.pop(0))
                gaps += 1
            said.append(len(script))
            script.append(sentence)
        r = replay(sc, embed, script, said, t, sp, every)
        hit += len(extra & r["skipped"])
        total += len(extra)
        wrong += len(r["skipped"] - extra)
        alerts += r["events"].count("jump")
    return hit, total, wrong, alerts, gaps


def going_back(sc, embed, data, every):
    """-> ("went back" alerted, times the speaker went back): each pair of 10-sentence blocks swapped."""
    caught = total = 0
    for s, t, sp in data:
        for first in range(0, len(s) - 19, 20):
            script = s[first + 10:first + 20] + s[first:first + 10]
            begin, end = sp[first][0], sp[first + 19][1]
            before = next((text for when, text in reversed(t) if when <= begin), "")
            cut = [(when, text[len(before):]) for when, text in t if begin < when <= end]
            said = list(range(10, 20)) + list(range(10))
            r = replay(sc, embed, script, said, cut, sp[first:first + 20], every)
            caught += "back" in r["events"]
            total += 1
    return caught, total


def off_script(sc, embed, data, every):
    """Share of updates flagged off script, when the script is someone else's."""
    statuses = []
    for k, (s, t, sp) in enumerate(data):
        script = next(d[0] for d in data[k + 1:] + data[:k] if d[0] != s)
        statuses += [x[4] for x in replay(sc, embed, script, [None] * len(s), t, sp, every)["log"]]
    return float(np.mean([x in OFF for x in statuses]))


def summary(sc, embed, data, every):
    runs = reading(sc, embed, data, every)
    cat = lambda k: [x for r in runs for x in r[k]]                                        # noqa: E731
    off, lags, events = np.array(cat("off")), cat("lags"), cat("events")
    skips = "; ".join(f"{size} at a time: {hit}/{total} found, {wrong} wrong, alerts {alerts}/{gaps}" for size in (1, 3)
                      for hit, total, wrong, alerts, gaps in [skipping(sc, embed, data, every, size)])
    back, backs = going_back(sc, embed, data, every)
    return runs, (f"exact {np.mean(off == 0):.1%} within1 {np.mean(off <= 1):.1%} lag {np.median(lags):.2f}/"
                  f"{np.percentile(lags, 90):.2f} s, reading: false alerts {len(events)}, "
                  f"off {np.mean([x[4] in OFF for r in runs for x in r['log']]):.1%} | skipping "
                  f"{skips} | back {back}/{backs} | off script "
                  f"{off_script(sc, embed, data, every):.1%}")


def ms(f, n):
    times = []
    for _ in range(n):
        start = time.perf_counter()
        result = f()
        times.append((time.perf_counter() - start) * 1000)
    return float(np.median(times)), result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--service", default=str(BENCH.parent))
    ap.add_argument("--split", default="test")
    ap.add_argument("--run", default="app", help="which saved run's transcripts to replay")
    ap.add_argument("--every", type=float, help="default: the service's SCRIPT_CHECK_S")
    ap.add_argument("--out", help="save the positions here (to check two versions agree)")
    args = ap.parse_args()
    sys.path.insert(0, args.service)
    from presentation_service import audio_analysis
    from presentation_service import script_compare as sc
    every = args.every or audio_analysis.SCRIPT_CHECK_S
    embed = sc.Embedder()
    data = load(args.split, args.run)
    embed(["warm up", "the embedder first"])
    runs, line = summary(sc, embed, data, every)
    print(line)
    spent = [x for r in runs for x in r["spent"]]
    print(f"update: p50 {np.median(spent):.2f} ms p95 {np.percentile(spent, 95):.2f} ms, {len(spent)} updates")
    sentences = [s for d in data for s in d[0]]
    said = " ".join(d[1][-1][1] for d in data).split()
    for size in (50, 250):
        chunks = sc.chunk_script(" ".join(sentences[:size]))
        build, tracker = ms(lambda: sc.ScriptTracker(chunks, embed), 5)
        words = said[:len(" ".join(sentences[:size]).split())]
        upd, _ = ms(lambda: tracker.update(" ".join(words), 0.0), 50)
        catch, _ = ms(lambda: (tracker.reset(), tracker.catch_up(" ".join(words[:1500]), 0.0)), 5)
        edit = ""
        if hasattr(tracker, "vectors"):
            changed = [*chunks[:-1], "And that was the very last part of it all."]
            edit = f"after an edit {ms(lambda: sc.ScriptTracker(changed, embed, known=tracker.vectors), 5)[0]:.0f} ms, "
        print(f"{len(chunks)} parts: build {build:.0f} ms, {edit}"
              f"update {upd:.2f} ms, "
              f"catch up on {min(1500, len(words))} words {catch:.0f} ms")
    if args.out:
        Path(args.out).write_text(json.dumps([[list(map(str, x)) for x in r["log"]] for r in runs]))
