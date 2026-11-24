"""
Build the benchmark tracks: 16 kHz mono WAVs of one person talking, each with a word-level reference.

  ami_*  AMI meeting corpus (CC BY 4.0): close-talk headset of one spontaneous, mostly non-native
         speaker. Only that person's turns of 4+ words are kept; everyone else's time becomes a short
         near-silence, so it sounds like one presenter. Every word (um/uh included) has a hand-checked
         start and end time.
  ds_*   DisfluencySpeech (Apache 2.0): one speaker re-enacting Switchboard in a studio. The annotation
         says which um/uh/like/so/you know are fillers and which are ordinary words ("I'd like to").

The dev tracks are for choosing settings, the test tracks (other speakers / other utterances) only for
reporting. About 700 MB of downloads, kept in --downloads.

  uv run --group bench python -m benchmarks.prepare_data
"""

import argparse
import io
import json
import re
import time
import urllib.error
import urllib.request
import wave
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np

SR = 16000
DATA = Path(__file__).parent / "data"
AMI_ANN = "https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip"
AMI_WAV = "https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/{m}/audio/{m}.Headset-{c}.wav"
DS_URL = "https://huggingface.co/datasets/amaai-lab/DisfluencySpeech/resolve/main/data/{f}-00000-of-00001.parquet"

# (meeting, speaker letter): different people in each split, from AMI's official dev / test meetings
AMI = {
    "dev": [("IB4004", "A"), ("IB4010", "D"), ("TS3004c", "B"), ("TS3004d", "D"), ("IB4005", "A")],
    "test": [("TS3003a", "A"), ("IS1009c", "C"), ("IS1009d", "A"), ("ES2004b", "B"),
             ("ES2004c", "C"), ("TS3003b", "D"), ("EN2002c", "C"), ("IS1009b", "D")],
}
AMI_SECONDS = 360            # length of each AMI track
REGION_GAP = 0.6             # words closer than this belong to the same turn
REGION_PAD = 0.3             # audio kept around a turn (word times are forced alignments)
TURN_GAP = 1.2               # near-silence between turns
MIN_TURN_WORDS = 4
DS_PER_TRACK = 50


def fetch(url, path):
    if not path.exists():
        print("downloading", url)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(path.suffix + ".part")
        for attempt in range(40):
            try:
                urllib.request.urlretrieve(url, part)
                break
            except (urllib.error.URLError, ConnectionError):    # the AMI server often answers 503
                if attempt == 39:
                    raise
                time.sleep(3)
        part.rename(path)
    return path


def write_track(name, split, audio, words):
    out = DATA / split
    out.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(out / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    (out / f"{name}.json").write_text(json.dumps({"duration": len(audio) / SR, "words": words}))
    print(f"{split}/{name}: {len(audio) / SR / 60:.1f} min, {len(words)} words, "
          f"{sum(w['w'] in ('um', 'uh') for w in words)} um/uh")


def near_silence(seconds, rng):
    return rng.normal(0, 1e-4, round(seconds * SR))


# ---------- AMI ----------
def ami_words(ann, meeting, speaker):
    root = ET.fromstring(ann.read(f"words/{meeting}.{speaker}.words.xml"))
    for e in root:
        if e.tag == "w" and e.get("punc") != "true" and e.text and e.get("starttime") and e.get("endtime"):
            for text in re.findall(r"[a-z0-9'_]+", e.text.lower().replace("-", " ")):
                # L_C_D_ -> one token per letter, the way an ASR would be scored
                yield {"w": text.strip("_").replace("_", " "), "s": float(e.get("starttime")),
                       "e": float(e.get("endtime"))}


def ami_channel(ann, meeting, speaker):
    for m in ET.fromstring(ann.read("corpusResources/meetings.xml")):
        for s in m:
            if m.get("observation") == meeting and s.get("nxt_agent") == speaker:
                return s.get("channel")
    raise KeyError((meeting, speaker))


def read_wav(path):
    with wave.open(str(path)) as w:
        assert w.getframerate() == SR and w.getsampwidth() == 2
        pcm = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, w.getnchannels())
        return pcm.mean(axis=1) / 32768


def build_ami(downloads, splits):
    rng = np.random.default_rng(0)
    with zipfile.ZipFile(fetch(AMI_ANN, downloads / "ami" / "ann.zip")) as ann:
        for split in splits:
            for meeting, speaker in AMI[split]:
                channel = ami_channel(ann, meeting, speaker)
                url = AMI_WAV.format(m=meeting, c=channel)
                audio = read_wav(fetch(url, downloads / "ami" / url.rsplit("/", 1)[1]))
                turns, turn = [], []
                for w in ami_words(ann, meeting, speaker):
                    if turn and w["s"] - turn[-1]["e"] > REGION_GAP:
                        turns.append(turn)
                        turn = []
                    turn.append(w)
                turns.append(turn)
                parts, words, t = [near_silence(1, rng)], [], 1.0
                for turn in turns:
                    if len(turn) < MIN_TURN_WORDS or t > AMI_SECONDS:
                        continue
                    start, end = max(0, turn[0]["s"] - REGION_PAD), turn[-1]["e"] + REGION_PAD
                    clip = audio[round(start * SR):round(end * SR)]
                    words += [{"w": w["w"], "s": round(w["s"] - start + t, 3),
                               "e": round(w["e"] - start + t, 3)} for w in turn]
                    parts += [clip, near_silence(TURN_GAP, rng)]
                    t += len(clip) / SR + TURN_GAP
                write_track(f"ami_{meeting}{speaker}", split, np.concatenate(parts), words)


# ---------- DisfluencySpeech ----------
def parse_annotated(text):
    """'{D Well, } I {F uh, } like it' -> [(well, D), (i, None), (uh, F), (like, None), ...]"""
    text = re.sub(r"<[^>]*>|\(\(|\)\)|[\[\]+/#]", " ", text)
    out, stack = [], []
    for tok in re.findall(r"\{[A-Z]|\}|[A-Za-z0-9'\-]+", text):
        if tok.startswith("{"):
            stack.append(tok[1])
        elif tok == "}":
            if stack:
                stack.pop()
        else:
            for w in re.findall(r"[a-z0-9']+", tok.lower()):
                out.append((w, stack[-1] if stack else None))
    return out


def build_ds(downloads, splits):
    import pyarrow.parquet as pq
    import soundfile as sf
    from scipy.signal import resample_poly
    rng = np.random.default_rng(1)
    for split in splits:
        name = {"dev": "validation", "test": "test"}[split]
        rows = pq.read_table(fetch(DS_URL.format(f=name), downloads / "ds" / f"{name}.parquet")).to_pylist()
        for k in range(0, len(rows), DS_PER_TRACK):
            parts, words, t = [near_silence(1, rng)], [], 1.0
            for row in rows[k:k + DS_PER_TRACK]:
                clip, rate = sf.read(io.BytesIO(row["audio"]["bytes"]))
                clip = resample_poly(clip, SR, rate)
                end = t + len(clip) / SR
                # no word times here: every word gets its utterance's span
                words += [{"w": w, "s": round(t, 3), "e": round(end, 3), "tag": tag}
                          for w, tag in parse_annotated(row["transcript_annotated"])]
                parts += [clip, near_silence(TURN_GAP, rng)]
                t = end + TURN_GAP
            write_track(f"ds_{k // DS_PER_TRACK}", split, np.concatenate(parts), words)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--downloads", type=Path, default=DATA / "downloads")
    ap.add_argument("--splits", nargs="+", default=["dev", "test"])
    args = ap.parse_args()
    build_ami(args.downloads, args.splits)
    build_ds(args.downloads, args.splits)
