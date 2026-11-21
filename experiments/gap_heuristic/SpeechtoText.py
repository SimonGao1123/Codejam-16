import re
import json
import time                                          # WHISPER (only for the speed print)
from collections import deque

import numpy as np                                   # WHISPER
import pyaudio
import webrtcvad
from faster_whisper import WhisperModel              # WHISPER
from vosk import Model, KaldiRecognizer

# ---------- settings ----------
FILLERS = ["um", "uh", "like", "you know", "so"]

SR = 16000
FRAME = 480                  # 480 samples = 30 ms (the size the VAD accepts)
FRAME_SEC = FRAME / SR       # 0.03 s
MIN_GAP = 0.25               # only look at gaps at least this long (seconds)
MIN_RUN = 0.15               # continuous voiced sound inside a gap = suspicious
VAD_LEVEL = 2                # 0-3, higher = stricter about what counts as speech
KEEP_SECONDS = 60            # how much history (voiced + audio) to keep

SUSPECT = {"a", "im", "ha", "ah", "on", "huh", "arm", "i'm"}        # near-misses for um/uh (add more as you find them)
MIN_DUR = 0.25               # a suspect word this long is probably a filler

USE_WHISPER = True                                   # WHISPER: False = plain VAD version
WHISPER_MODEL = "tiny.en"                            # WHISPER: "base.en" = more accurate, slower
PROMPT = "Umm, let me think, hmm... Okay, so, uh, I think, you know, it's, um, fine."   
FILLER_MAP = {"umm": "um", "um": "um", "uhh": "uh", "uh": "uh",
              "erm": "um", "er": "uh", "ah": "uh", "hmm": "um", "mm": "um"}            

# ---------- state ----------
frame_count = 0              # our clock: one tick per frame read
prev_end = 0.0               # end time of the last recognized word
transcript = []              # every finished sentence (as word lists), in order

vad = webrtcvad.Vad(VAD_LEVEL)
voiced = deque()             # one True/False per frame (speech or not)
audio = deque()              # WHISPER: raw bytes, one entry per frame
base = 0                     # absolute frame number of voiced[0] / audio[0]
MAX_FRAMES = int(KEEP_SECONDS / FRAME_SEC)

whisper = WhisperModel(WHISPER_MODEL, compute_type="int8") if USE_WHISPER else None


# ---------- helpers ----------
def longest_voiced_run(start_t, end_t):
    """Longest stretch of consecutive 'speech' frames between two times (seconds)."""
    a = max(int(start_t / FRAME_SEC), base)
    b = min(int(end_t / FRAME_SEC), base + len(voiced))
    best = cur = 0
    for i in range(a, b):
        if voiced[i - base]:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best * FRAME_SEC


def get_clip(start_t, end_t, pad=0.1):                                   # WHISPER
    """Cut the sound between two times out of the audio history (floats Whisper accepts)."""
    a = max(int((start_t - pad) / FRAME_SEC), base)
    b = min(int((end_t + pad) / FRAME_SEC), base + len(audio))
    chunk = [audio[i - base] for i in range(a, b)]
    if not chunk:
        return None
    pcm = np.frombuffer(b"".join(chunk), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def classify_clip(clip):                                                 # WHISPER
    """Ask Whisper what's in a short clip. Returns 'um', 'uh', or None."""
    segments, _ = whisper.transcribe(
        clip, language="en", initial_prompt=PROMPT, beam_size=1,
        vad_filter=False, condition_on_previous_text=False,
    )
    words = [re.sub(r"[^a-z]", "", w.lower()) for s in segments for w in s.text.split()]
    for w in words:
        if w in FILLER_MAP:
            return FILLER_MAP[w]
    return None


def mark_filler(label, tokens, totals):                                  # WHISPER
    """Count a filler, print an alert, and put a marker in the transcript."""
    totals[label] += 1
    print(f"\033[K --STOP-- {label}, counter: {totals[label]}")
    tokens.append("[um?]" if label == "filler?" else f"[{label}]")


def count_fillers(text):
    return {f: len(re.findall(rf"\b{re.escape(f)}\b", text)) for f in FILLERS}


def alert_filler(counts, alerted):
    for word, n in counts.items():
        while alerted[word] < n:
            alerted[word] += 1
            print(f"\033[K --STOP-- don't say: {word}, counter: {alerted[word]}")


def finish_phrase(res, alerted, totals):
    global prev_end
    text = res.get("text", "")

    counts = count_fillers(text)
    alert_filler(counts, alerted)
    for w in FILLERS:
        totals[w] += counts[w]
        alerted[w] = 0

    words = res.get("result", [])
    tokens = []                                   # this sentence, word by word
    for w in words:
        # ----- check 1: a gap where Vosk heard nothing but the VAD hears voice -----
        gap = w["start"] - prev_end
        if gap >= MIN_GAP:
            run = longest_voiced_run(prev_end, w["start"])
            label = None
            if run >= MIN_RUN:
                if USE_WHISPER:                                          # WHISPER: second opinion
                    clip = get_clip(prev_end, w["start"])
                    label = classify_clip(clip) if clip is not None else None
                else:
                    label = "filler?"
            print(f"\033[K [gap] {gap:.2f}s before '{w['word']}', voiced run {run:.2f}s"
                  + (f"  -> {label}" if label else ""))
            if label:
                mark_filler(label, tokens, totals)

        # ----- check 2: a long "a"/"im" that is really an um/uh -----
        dur = w["end"] - w["start"]
        label = None
        if w["word"] in SUSPECT:
            print(f"\033[K [suspect] '{w['word']}' dur {dur:.2f}s conf {w.get('conf', 0):.2f}")
            if dur >= MIN_DUR:
                if USE_WHISPER:                                          # WHISPER: second opinion
                    clip = get_clip(w["start"], w["end"])
                    label = classify_clip(clip) if clip is not None else None
                else:
                    label = "filler?"
        if label:
            mark_filler(label, tokens, totals)      # replaces the wrong word
        else:
            tokens.append(w["word"])                # keep the word as Vosk heard it

        prev_end = w["end"]

    if tokens:
        transcript.append(tokens)

    if words:
        now = frame_count * FRAME_SEC
        print(f"\033[K [clock] last word ended {words[-1]['end']:.2f}s, now {now:.2f}s")


def build_paragraph(sentences):
    out = []
    for tokens in sentences:
        tokens = tokens[:]
        for i, t in enumerate(tokens):
            if not t.startswith("["):             # capitalize the first real word
                tokens[i] = t.capitalize()
                break
        out.append(" ".join(tokens) + ".")
    paragraph = " ".join(out)
    return re.sub(r"\bi\b", "I", paragraph)       # lone "i" -> "I" (also i'm, i'll)


# ---------- setup ----------
model = Model("vosk-model-small-en-us-0.15")
recognizer = KaldiRecognizer(model, SR)
recognizer.SetWords(True)

# if USE_WHISPER:                                                          # WHISPER: speed test, delete later
#     t0 = time.time()
#     classify_clip(np.zeros(8000, dtype=np.float32))
#     print(f"Whisper test clip took {time.time() - t0:.2f}s")

mic = pyaudio.PyAudio()
stream = mic.open(
    format=pyaudio.paInt16, channels=1, rate=SR,
    input=True, frames_per_buffer=FRAME,
)
stream.start_stream()

alerted = {f: 0 for f in FILLERS}
totals = {f: 0 for f in FILLERS}
totals["filler?"] = 0

last_partial = ""

print("Listening... (say something, Ctrl+C to stop)")

try:
    while True:
        data = stream.read(FRAME, exception_on_overflow=False)
        frame_count += 1

        audio.append(data)                                  # WHISPER: keep the real sound too
        voiced.append(vad.is_speech(data, SR))
        if len(voiced) > MAX_FRAMES:
            voiced.popleft()
            audio.popleft()                                 # WHISPER: trim together so frame numbers match
            base += 1

        if recognizer.AcceptWaveform(data):
            res = json.loads(recognizer.Result())
            text = res.get("text", "")
            if text:
                print(f"\033[KFinal: {text}")
            finish_phrase(res, alerted, totals)
            last_partial = ""
        else:
            partial_text = json.loads(recognizer.PartialResult()).get("partial", "")
            if partial_text and partial_text != last_partial:
                print(f"\033[KPartial: {partial_text}", end="\r")
                alert_filler(count_fillers(partial_text), alerted)
                last_partial = partial_text

except KeyboardInterrupt:
    res = json.loads(recognizer.FinalResult())
    finish_phrase(res, alerted, totals)
    print(f"\nStopped. Total fillers: {sum(totals.values())}")
    print(totals)

    paragraph = build_paragraph(transcript)
    print("\n--- Full transcript ---")
    print(paragraph)
    # with open("transcript.txt", "w") as f:
    #     f.write(paragraph)
finally:
    stream.stop_stream()
    stream.close()
    mic.terminate()