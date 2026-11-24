"""
Run one transcription engine over the benchmark tracks, the way the app runs it: 30 ms frames pushed
through Transcriber.feed(), one engine per process (so memory and CPU are its own).

Audio is fed as fast as it's processed, but every feed() is timed; score.py replays those times against
the audio clock to get the delay a live speaker would see (including any backlog).

  uv run --group bench python -m benchmarks.run moonshine-medium --split test
"""

import argparse
import json
import logging
import resource
import time
import wave
from pathlib import Path

import numpy as np

BENCH = Path(__file__).parent
REPO = BENCH.parent.parent
VOSK_SMALL = REPO / "experiments/gap_heuristic/vosk-model-small-en-us-0.15"
VOSK_LARGE = REPO / "models/vosk-model-en-us-0.22"
FRAME = 480
NO_ECHO = {"return_audio_data": "false"}


def vosk(path, whisper=None, text_only=False):
    from . import vosk_pipeline as vp
    model = vp.load_vosk(path)
    t = vp.Transcriber(model, vp.load_whisper(whisper) if whisper else None)
    if text_only:                       # only what Vosk writes itself, without the suspect checks
        t.classify = lambda *_: None
    return t


def moonshine(arch, **kwargs):
    from moonshine_voice import ModelArch, get_model_for_language
    from moonshine_voice import Transcriber as MoonshineTranscriber
    from .moonshine_baseline import Transcriber
    path, arch = get_model_for_language("en", ModelArch[arch])
    return Transcriber(MoonshineTranscriber(model_path=path, model_arch=arch, **kwargs))


def app(interval=None, keyterms=None, boost=None):
    """The app's current transcriber, optionally with settings to try out."""
    from moonshine_voice import Transcriber as MoonshineTranscriber
    from moonshine_voice import get_model_for_language
    from presentation_service import transcript
    if interval:
        transcript.UPDATE_INTERVAL = interval
    model = transcript.load_moonshine()
    if boost is not None:
        path, arch = get_model_for_language("en")
        model = MoonshineTranscriber(model_path=path, model_arch=arch,
                                     options={**NO_ECHO, "keyterm_boost": str(boost)})
        model.set_keyterms(transcript.KEYTERMS)
    if keyterms is not None:                        # "" turns the biasing off
        model.set_keyterms([t for t in keyterms.split(",") if t])
    return transcript.Transcriber(model)


ENGINES = {
    "vosk-small": lambda: vosk(VOSK_SMALL, text_only=True),
    "vosk-large": lambda: vosk(VOSK_LARGE, text_only=True),
    "vosk-small+whisper": lambda: vosk(VOSK_SMALL, "tiny.en"),
    "vosk-large+whisper": lambda: vosk(VOSK_LARGE, "tiny.en"),
    "moonshine-medium": lambda: moonshine("MEDIUM_STREAMING"),     # the app before this benchmark
    # the models compared without the audio echo (it slows every size down alike)
    "moonshine-tiny-noecho": lambda: moonshine("TINY_STREAMING", options=NO_ECHO),
    "moonshine-small-noecho": lambda: moonshine("SMALL_STREAMING", options=NO_ECHO),
    "moonshine-medium-noecho": lambda: moonshine("MEDIUM_STREAMING", options=NO_ECHO),
    "app": app,                                                    # the app's current code
}


def cpu_seconds():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6      # macOS reports bytes


def load_track(path):
    with wave.open(str(path)) as w:
        return w.readframes(w.getnframes())


def run_track(t, pcm):
    t.reset()
    frame_ms, events = [], []
    cpu = cpu_seconds()
    for i in range(0, len(pcm) - FRAME * 2 + 1, FRAME * 2):
        start = time.perf_counter()
        out = t.feed(pcm[i:i + FRAME * 2])
        frame_ms.append((time.perf_counter() - start) * 1000)
        events += [(len(frame_ms) - 1, kind, value) for kind, value in out]
    start = time.perf_counter()
    out = t.flush()
    flush_ms = (time.perf_counter() - start) * 1000
    events += [(len(frame_ms), kind, value) for kind, value in out]
    return {
        "seconds": len(frame_ms) * FRAME / 16000,
        "frame_ms": [round(x, 3) for x in frame_ms],
        "flush_ms": flush_ms,
        "cpu_s": cpu_seconds() - cpu,
        "events": events,
        "sentences": t.sentences,
        "totals": t.totals,
        "filler_checkpoints": t.filler_checkpoints,
        "word_times": t.word_times,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("engine", choices=ENGINES)
    ap.add_argument("--split", default="test")
    ap.add_argument("--tracks", help="only these tracks (comma-separated name parts)")
    ap.add_argument("--tag", help="name to save the results under (default: the engine)")
    ap.add_argument("--interval", type=float, help="app engine: Moonshine update interval")
    ap.add_argument("--keyterms", help="app engine: comma-separated terms to bias towards")
    ap.add_argument("--boost", type=float, help="app engine: keyterm boost")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    start = time.perf_counter()
    t = app(args.interval, args.keyterms, args.boost) if args.engine == "app" else ENGINES[args.engine]()
    load_s = time.perf_counter() - start
    # warm up so the first track isn't charged for lazy initialisation
    for _ in range(100):
        t.feed(np.zeros(FRAME, "<i2").tobytes())
    result = {"engine": args.engine, "split": args.split, "load_s": load_s, "rss_loaded_mb": rss_mb(),
              "tracks": {}}
    for path in sorted((BENCH / "data" / args.split).glob("*.wav")):
        if args.tracks and not any(part in path.name for part in args.tracks.split(",")):
            continue
        r = result["tracks"][path.stem] = run_track(t, load_track(path))
        print(f"{args.engine} {path.stem}: {r['seconds']:.0f}s audio in {sum(r['frame_ms']) / 1000:.1f}s")
    result["rss_peak_mb"] = rss_mb()
    out = BENCH / "results" / f"{args.tag or args.engine}_{args.split}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result))
