import re
import json
from collections import deque
import pyaudio
import webrtcvad                      # VAD 1: new import
from vosk import Model, KaldiRecognizer

FILLERS = ["um", "uh", "like", "you know"]

SR = 16000
FRAME = 480                  # 480 samples = 30 ms (the size the VAD accepts)
FRAME_SEC = FRAME / SR       # 0.03 s
MIN_GAP = 0.25               # only look at gaps at least this long (seconds)
MIN_RUN = 0.15               # VAD 2: this much continuous voiced sound inside a gap = filler?
VAD_LEVEL = 2                # VAD 2: 0-3, higher = stricter about what counts as speech
KEEP_SECONDS = 60            # VAD 2: how much voiced history to keep

frame_count = 0              # our clock: one tick per frame read
prev_end = 0.0               # end time of the last recognized word
transcript = []              # every finished sentence, in order

vad = webrtcvad.Vad(VAD_LEVEL)              # VAD 3: create the detector
voiced = deque()                            # VAD 4: one True/False per frame (speech or not)
base = 0                                    # VAD 4: absolute frame number of voiced[0]
MAX_FRAMES = int(KEEP_SECONDS / FRAME_SEC)  # VAD 4: cap so the list doesn't grow forever


def longest_voiced_run(start_t, end_t):     # VAD 5: new helper
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
    tokens = []                                   # NEW: this sentence, word by word
    for w in words:
        gap = w["start"] - prev_end
        if gap >= MIN_GAP:
            run = longest_voiced_run(prev_end, w["start"])
            is_filler = run >= MIN_RUN
            print(f"\033[K [gap] {gap:.2f}s before '{w['word']}', "
                  f"voiced run {run:.2f}s" + ("  -> filler?" if is_filler else ""))
            if is_filler:
                totals["filler?"] += 1
                print(f"\033[K --STOP-- possible um/uh, counter: {totals['filler?']}")
                tokens.append("[um?]")            # NEW: marker goes before the word after the gap
        tokens.append(w["word"])                  # NEW
        prev_end = w["end"]

    if tokens:                                    # NEW: store the token list, not the plain text
        transcript.append(tokens)

    if words:
        now = frame_count * FRAME_SEC
        print(f"\033[K [clock] last word ended {words[-1]['end']:.2f}s, now {now:.2f}s")

def build_paragraph(sentences):
    out = []
    for tokens in sentences:
        tokens = tokens[:]                        # copy so we don't change the original
        for i, t in enumerate(tokens):
            if t != "[um?]":                      # capitalize the first real word
                tokens[i] = t.capitalize()
                break
        out.append(" ".join(tokens) + ".")
    paragraph = " ".join(out)
    return re.sub(r"\bi\b", "I", paragraph)       # lone "i" -> "I" (also fixes i'm, i'll)


model = Model("vosk-model-small-en-us-0.15")
recognizer = KaldiRecognizer(model, SR)
recognizer.SetWords(True)

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

        voiced.append(vad.is_speech(data, SR))   # VAD 8: label this frame speech / not speech
        if len(voiced) > MAX_FRAMES:             # VAD 8: drop the oldest frame, shift base
            voiced.popleft()
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
finally:
    stream.stop_stream()
    stream.close()
    mic.terminate()