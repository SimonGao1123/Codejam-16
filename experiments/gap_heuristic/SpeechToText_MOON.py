import re                                                       # NEW
import numpy as np
import sounddevice as sd
import noisereduce as nr
from moonshine_voice import Transcriber, TranscriptEventListener, get_model_for_language

SR = 16000
CHUNK = 1600                      # 0.1 s of audio per read

# NEW: what counts as a filler (matched against lowercase text)
FILLER_PATTERNS = {
    "um": r"\bum\b",
    "uh": r"\buh\b",
    "erm": r"\berm\b",
    "hmm": r"\bhmm\b",
    "like": r"\blike\b",
    "you know": r"\byou know\b",
}
totals = {k: 0 for k in FILLER_PATTERNS}                        # NEW: running totals from finished lines
transcript = []

END_PUNCT = (".", ",", "?", "!")     # don't add a period after any of these
def build_paragraph(sentences):
    out = []
    for tokens in sentences:
        if not tokens:                          # skip empty lines
            continue
        sentence = "".join(tokens)
        if not sentence.endswith(END_PUNCT):    # only add "." when nothing is there already
            sentence += "."
        out.append(sentence)
    paragraph = " ".join(out)
    return re.sub(r"\bi\b", "I", paragraph)     # lone "i" -> "I" (also i'm, i'll)


def count_fillers(text):                                        # NEW
    text = text.lower()
    return {k: len(re.findall(p, text)) for k, p in FILLER_PATTERNS.items()}


# 1. Learn what the room sounds like
print("Stay quiet for 2 seconds...")
noise = sd.rec(int(2 * SR), samplerate=SR, channels=1, dtype="float32")
sd.wait()
noise = noise[:, 0]

# 2. Set up Moonshine
model_path, model_arch = get_model_for_language("en")
transcriber = Transcriber(model_path=model_path, model_arch=model_arch)


class Listener(TranscriptEventListener):
    def __init__(self):                                         # NEW
        self.alerted = {k: 0 for k in FILLER_PATTERNS}          # NEW: alerts already shown for the current line

    def on_line_text_changed(self, event):
        text = event.line.text
        print(f"\033[KPartial: {text}", end="\r", flush=True)
        for name, n in count_fillers(text).items():             # NEW: instant alerts from partial text
            while self.alerted[name] < n:
                self.alerted[name] += 1
                print(f"\033[K --STOP-- don't say: {name} (count {totals[name] + self.alerted[name]})")

    def on_line_completed(self, event):
        text = event.line.text
        transcript.append(text)
        print(f"\033[KFinal: {text}")
        for name, n in count_fillers(text).items():             # NEW: totals come from final text only
            totals[name] += n
            self.alerted[name] = 0                              # reset for the next line


stream = transcriber.create_stream(update_interval=0.5)
stream.add_listener(Listener())                                 # NEW: removed the duplicate transcriber.add_listener
stream.start()

# 3. Mic -> clean -> Moonshine
try:
    with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=CHUNK) as mic:
        print("Listening... Ctrl+C to stop")
        while True:
            chunk, _ = mic.read(CHUNK)
            chunk = nr.reduce_noise(y=chunk[:, 0], sr=SR, y_noise=noise, stationary=True)
            stream.add_audio(chunk.astype(np.float32), SR)
except KeyboardInterrupt:
    stream.stop()
    stream.close()
    print(f"\nTotal fillers: {sum(totals.values())}")           # NEW
    print(totals)                                               # NEW

    paragraph = build_paragraph(transcript)
    print("\n--- Full transcript ---")
    print(paragraph)