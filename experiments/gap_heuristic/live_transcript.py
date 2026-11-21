"""
Live mic transcript with um/uh detection.

How it works:
  1. Silero VAD listens to the mic and cuts the audio into utterances
     (you talk, you pause for ~0.6s -> one utterance).
     While you're still talking, the utterance so far is re-transcribed every
     ~0.5s and redrawn in place (dim text), so words show up as you speak.
  2. On the pause, faster-whisper transcribes the whole utterance with word
     timestamps and the live line is replaced by the final one.
  3. Gap heuristic: any part of the utterance where VAD heard speech but
     Whisper wrote no word is a likely "um"/"uh" -> printed as [um?].
  4. If Whisper itself writes "um"/"uh" (the prompt nudges it to), that's
     printed as [um] too.

Run:   uv run live_transcript.py
       uv run live_transcript.py --model base.en      (faster, less accurate)
       uv run live_transcript.py --file clip.wav      (test on a recording)
"""

import argparse
import queue
import re
import shutil
import time

import numpy as np
import sounddevice as sd
import torch
from faster_whisper import WhisperModel, decode_audio
from silero_vad import VADIterator, get_speech_timestamps, load_silero_vad

SAMPLE_RATE = 16000
BLOCK = 512                 # silero needs exactly 512 samples per call at 16kHz
PRE_ROLL_BLOCKS = 10        # keep ~0.3s before speech starts so the first word isn't cut
MAX_UTTERANCE_S = 12        # force a transcription if someone talks without pausing
PARTIAL_EVERY_S = 0.5       # refresh the live line after this much new audio (slows down if CPU can't keep up)

# gap heuristic tuning
GAP_MIN_S = 0.20            # shorter gaps are just timestamp noise
GAP_MAX_S = 1.50            # longer gaps are usually a missed word, not a filler
GAP_ENERGY_RATIO = 0.35     # gap must be at least this loud vs the utterance (skips breaths)

FILLER_PROMPT = "Um, uh, so, like, I mean, um, you know, uh, basically."
FILLER_WORDS = re.compile(r"^(um+|uh+|uhm|erm?|ah+|hmm+|mm+)$")

RESET, DIM, YELLOW, RED, BOLD = "\033[0m", "\033[2m", "\033[33m", "\033[31m", "\033[1m"


def rms(x):
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0


def is_filler(word):
    return bool(FILLER_WORDS.match(re.sub(r"[^a-z]", "", word.lower())))


def uncovered(region, words):
    """Parts of a speech region that no transcribed word covers."""
    start, end = region["start"], region["end"]
    cur, gaps = start, []
    for w in words:
        if w.end < start or w.start > end:
            continue
        if w.start > cur:
            gaps.append((cur, w.start))
        cur = max(cur, w.end)
    if end > cur:
        gaps.append((cur, end))
    return gaps


def find_gap_fillers(audio, words, vad_model):
    speech = get_speech_timestamps(torch.from_numpy(audio), vad_model,
                                   sampling_rate=SAMPLE_RATE, return_seconds=True)
    loudness = rms(audio)
    fillers = []
    for region in speech:
        for s, e in uncovered(region, words):
            if not GAP_MIN_S <= e - s <= GAP_MAX_S:
                continue
            chunk = audio[int(s * SAMPLE_RATE):int(e * SAMPLE_RATE)]
            if rms(chunk) >= GAP_ENERGY_RATIO * loudness:
                fillers.append((s, e))
    return fillers


def show_partial(audio, model):
    """Quick transcript of the utterance so far, redrawn in place on the current line."""
    t0 = time.perf_counter()
    segments, _ = model.transcribe(audio, language="en", beam_size=1,
                                   initial_prompt=FILLER_PROMPT,
                                   condition_on_previous_text=False)
    text = " ".join(seg.text.strip() for seg in segments)

    # \r only rewinds one terminal row, so a wrapped line can't be redrawn: show the tail instead
    width = shutil.get_terminal_size().columns - 1
    if len(text) > width:
        text = "…" + text[-(width - 1):]
    print(f"\r\033[K{DIM}{text}{RESET}", end="", flush=True)
    return time.perf_counter() - t0


def transcribe(audio, model, vad_model, stats):
    t0 = time.perf_counter()
    segments, _ = model.transcribe(audio, language="en", word_timestamps=True,
                                   initial_prompt=FILLER_PROMPT,
                                   condition_on_previous_text=False)
    words = [w for seg in segments for w in (seg.words or [])]
    gaps = find_gap_fillers(audio, words, vad_model)

    # if whisper already wrote the um, the leftover gap next to it is the same filler
    said = [w for w in words if is_filler(w.word)]
    gaps = [(s, e) for s, e in gaps
            if not any(w.start - 0.3 <= e and w.end + 0.3 >= s for w in said)]
    took = time.perf_counter() - t0

    # merge words and gap fillers into one timeline so [um?] shows where it happened
    timeline = [(w.start, "word", w.word.strip()) for w in words]
    timeline += [(s, "gap", f"{e - s:.1f}s") for s, e in gaps]
    timeline.sort(key=lambda t: t[0])

    out, n_text, n_gap = [], 0, 0
    for _, kind, text in timeline:
        if kind == "gap":
            out.append(f"{RED}[um?]{RESET}")
            n_gap += 1
        elif is_filler(text):
            out.append(f"{YELLOW}[{text.strip(',.')}]{RESET}")
            n_text += 1
        else:
            out.append(text)

    stats["text"] += n_text
    stats["gap"] += n_gap
    stats["words"] += len(words)
    stats["speech_s"] += len(audio) / SAMPLE_RATE

    print("\r\033[K", end="")    # clear the live line before printing the final one
    if out:
        print(" ".join(out), f"{DIM}({len(audio) / SAMPLE_RATE:.1f}s audio, {took:.1f}s to process){RESET}")


def blocks_from_mic(q):
    while True:
        yield q.get()


def blocks_from_file(path):
    audio = decode_audio(path, sampling_rate=SAMPLE_RATE)
    for i in range(0, len(audio) - BLOCK + 1, BLOCK):
        yield audio[i:i + BLOCK]
    yield np.zeros(SAMPLE_RATE, dtype=np.float32)  # trailing silence closes the last utterance


def run(blocks, model, stats):
    # two VAD models: the iterator keeps streaming state, get_speech_timestamps resets state
    stream_vad = load_silero_vad()
    gap_vad = load_silero_vad()
    vad = VADIterator(stream_vad, sampling_rate=SAMPLE_RATE, min_silence_duration_ms=600)

    pre_roll, utterance, talking = [], [], False
    next_partial = 0            # utterance length (in chunks) at which to refresh the live line
    partial_chunks = int(PARTIAL_EVERY_S * SAMPLE_RATE / BLOCK)
    for block in blocks:
        for i in range(0, len(block) - BLOCK + 1, BLOCK):
            chunk = block[i:i + BLOCK]
            event = vad(torch.from_numpy(chunk))

            if event and "start" in event and not talking:
                talking = True
                utterance = pre_roll[:]
                next_partial = len(utterance) + partial_chunks

            if talking:
                utterance.append(chunk)
            else:
                pre_roll = (pre_roll + [chunk])[-PRE_ROLL_BLOCKS:]

            too_long = len(utterance) * BLOCK >= MAX_UTTERANCE_S * SAMPLE_RATE
            if talking and ((event and "end" in event) or too_long):
                transcribe(np.concatenate(utterance), model, gap_vad, stats)
                talking, utterance, pre_roll = False, [], []
                if too_long:
                    vad.reset_states()
            elif talking and len(utterance) >= next_partial:
                took = show_partial(np.concatenate(utterance), model)
                # if transcribing is slow, refresh less often so we don't fall behind the mic
                wait_chunks = max(partial_chunks, int(1.5 * took * SAMPLE_RATE / BLOCK))
                next_partial = len(utterance) + wait_chunks


def load_whisper(name, device):
    # mps = Apple Silicon GPU (experimental in ctranslate2, ~3x faster than cpu on an M5)
    if device in ("auto", "mps"):
        try:
            return WhisperModel(name, device="mps", compute_type="int8_float16"), "mps"
        except Exception:
            if device == "mps":
                raise
    # "auto" here picks cuda on an nvidia machine, cpu otherwise
    model = WhisperModel(name, device="auto" if device == "auto" else device, compute_type="int8")
    return model, model.model.device


def print_summary(stats, wall_s):
    minutes = max(stats["speech_s"], 1) / 60
    total = stats["text"] + stats["gap"]
    print(f"\n{BOLD}--- summary ---{RESET}")
    print(f"session: {wall_s:.0f}s   speech: {stats['speech_s']:.0f}s   words: {stats['words']}")
    print(f"pace: {stats['words'] / minutes:.0f} words/min (of speech time)")
    print(f"fillers: {total}  ({YELLOW}{stats['text']} transcribed{RESET}, "
          f"{RED}{stats['gap']} from gaps{RESET})  ->  {total / minutes:.1f} per min")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="small.en",
                        help="tiny.en / base.en / small.en / distil-large-v3")
    parser.add_argument("--device", default="auto", help="auto / mps / cuda / cpu")
    parser.add_argument("--file", help="process a recording instead of the mic")
    args = parser.parse_args()

    # silero is tiny; letting torch grab every core fights with whisper and slows both down
    torch.set_num_threads(1)

    print(f"loading {args.model} ...")
    model, device = load_whisper(args.model, args.device)
    print(f"whisper running on {device}")
    stats = {"text": 0, "gap": 0, "words": 0, "speech_s": 0.0}
    t0 = time.time()

    try:
        if args.file:
            run(blocks_from_file(args.file), model, stats)
        else:
            q = queue.Queue()
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                blocksize=BLOCK, callback=lambda d, *_: q.put(d[:, 0].copy())):
                print(f"{BOLD}listening... talk, pause to see each line. ctrl+c to stop.{RESET}")
                print(f"{YELLOW}[um]{RESET} = whisper wrote it   {RED}[um?]{RESET} = gap heuristic\n")
                run(blocks_from_mic(q), model, stats)
    except KeyboardInterrupt:
        pass

    print_summary(stats, time.time() - t0)


if __name__ == "__main__":
    main()
