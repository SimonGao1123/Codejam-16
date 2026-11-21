"""
Live transcript with filler-word detection, from experiments/gap_heuristic/SpeechtoText.py.
Same method, restructured to take audio pushed in from the Electron app instead of reading the mic:

  - Vosk gives the running transcript (partial words while you talk, a final sentence on a pause)
    and counts the filler words it writes ("um", "uh", "like", "you know", "so").
  - Vosk often drops um/uh. Two checks catch those:
      1. a gap between words where the webrtcvad still heard voice
      2. a long near-miss word ("a", "im", "ah", ...) that is probably really um/uh
    Each suspicious clip gets a second opinion from Whisper (prompted to write fillers).
    Without Whisper, both checks mark "[um?]" instead.
"""

import json
import logging
import re
from collections import deque

import numpy as np
import webrtcvad
from vosk import KaldiRecognizer, Model

log = logging.getLogger(__name__)

# ---------- settings ----------
FILLERS = ["um", "uh", "like", "you know", "so"]

SR = 16000
FRAME = 480                  # 480 samples = 30 ms (the size the VAD accepts)
FRAME_SEC = FRAME / SR       # 0.03 s
MIN_GAP = 0.25               # only look at gaps at least this long (seconds)
MIN_RUN = 0.15               # continuous voiced sound inside a gap = suspicious
VAD_LEVEL = 2                # 0-3, higher = stricter about what counts as speech
KEEP_SECONDS = 60            # how much history (voiced + audio) to keep
MAX_FRAMES = int(KEEP_SECONDS / FRAME_SEC)

SUSPECT = {"a", "im", "ha", "ah", "on", "huh", "arm", "i'm"}        # near-misses for um/uh
MIN_DUR = 0.25               # a suspect word this long is probably a filler

PROMPT = "Umm, let me think, hmm... Okay, so, uh, I think, you know, it's, um, fine."
FILLER_MAP = {"umm": "um", "um": "um", "uhh": "uh", "uh": "uh",
              "erm": "um", "er": "uh", "ah": "uh", "hmm": "um", "mm": "um"}


def count_fillers(text):
    return {f: len(re.findall(rf"\b{re.escape(f)}\b", text)) for f in FILLERS}


def sentence_text(tokens):
    """['so', 'i', 'think', '[um]'] -> 'So I think [um].'"""
    tokens = tokens[:]
    for i, t in enumerate(tokens):
        if not t.startswith("["):             # capitalize the first real word
            tokens[i] = t.capitalize()
            break
    return re.sub(r"\bi\b", "I", " ".join(tokens) + ".")      # lone "i" -> "I" (also i'm, i'll)


class Transcriber:
    """Feed 30 ms frames of 16 kHz int16 audio; returns transcript events as they happen."""

    def __init__(self, model, whisper=None):
        self.model = model
        self.whisper = whisper                # faster_whisper.WhisperModel, or None
        self.vad = webrtcvad.Vad(VAD_LEVEL)
        self.reset()

    def reset(self):
        self.recognizer = KaldiRecognizer(self.model, SR)
        self.recognizer.SetWords(True)
        self.frame_count = 0                  # our clock: one tick per frame
        self.prev_end = 0.0                   # end time of the last recognized word
        self.voiced = deque()                 # one True/False per frame (speech or not)
        self.audio = deque()                  # raw bytes, one entry per frame (for Whisper)
        self.base = 0                         # absolute frame number of voiced[0] / audio[0]
        self.sentences = []                   # every finished sentence, as text
        self.totals = {f: 0 for f in FILLERS} | {"filler?": 0}
        self.alerted = {f: 0 for f in FILLERS}    # fillers already alerted from this phrase's partials
        self.last_partial = ""

    # ---------- helpers ----------
    def longest_voiced_run(self, start_t, end_t):
        """Longest stretch of consecutive 'speech' frames between two times (seconds)."""
        a = max(int(start_t / FRAME_SEC), self.base)
        b = min(int(end_t / FRAME_SEC), self.base + len(self.voiced))
        best = cur = 0
        for i in range(a, b):
            if self.voiced[i - self.base]:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        return best * FRAME_SEC

    def get_clip(self, start_t, end_t, pad=0.1):
        """Cut the sound between two times out of the audio history (floats Whisper accepts)."""
        a = max(int((start_t - pad) / FRAME_SEC), self.base)
        b = min(int((end_t + pad) / FRAME_SEC), self.base + len(self.audio))
        chunk = [self.audio[i - self.base] for i in range(a, b)]
        if not chunk:
            return None
        return np.frombuffer(b"".join(chunk), dtype=np.int16).astype(np.float32) / 32768.0

    def classify(self, start_t, end_t):
        """'um' / 'uh' if Whisper hears a filler in that clip, None if not; 'filler?' without Whisper."""
        if self.whisper is None:
            return "filler?"
        clip = self.get_clip(start_t, end_t)
        if clip is None:
            return None
        segments, _ = self.whisper.transcribe(
            clip, language="en", initial_prompt=PROMPT, beam_size=1,
            vad_filter=False, condition_on_previous_text=False,
        )
        words = [re.sub(r"[^a-z]", "", w.lower()) for s in segments for w in s.text.split()]
        return next((FILLER_MAP[w] for w in words if w in FILLER_MAP), None)

    def new_fillers(self, counts):
        """Fillers in `counts` beyond what was already alerted for this phrase (one per occurrence)."""
        hits = []
        for word, n in counts.items():
            while self.alerted[word] < n:
                self.alerted[word] += 1
                hits.append(word)
        return hits

    # ---------- main ----------
    def feed(self, frame):
        """One 30 ms frame (480 int16 samples as bytes) -> list of events:
        ("partial", text), ("sentence", text), ("filler", word)."""
        self.frame_count += 1
        self.audio.append(frame)
        self.voiced.append(self.vad.is_speech(frame, SR))
        if len(self.voiced) > MAX_FRAMES:
            self.voiced.popleft()
            self.audio.popleft()                 # trim together so frame numbers match
            self.base += 1

        if self.recognizer.AcceptWaveform(frame):
            events = self.finish_phrase(json.loads(self.recognizer.Result()))
            self.last_partial = ""
            return events + [("partial", "")]
        partial = json.loads(self.recognizer.PartialResult()).get("partial", "")
        if partial == self.last_partial:
            return []
        self.last_partial = partial
        return [("partial", partial)] + [("filler", w) for w in self.new_fillers(count_fillers(partial))]

    def flush(self):
        """End of the session: finish whatever phrase is still open."""
        return self.finish_phrase(json.loads(self.recognizer.FinalResult()))

    def finish_phrase(self, res):
        events = []
        counts = count_fillers(res.get("text", ""))
        events += [("filler", w) for w in self.new_fillers(counts)]     # ones the partials didn't show
        for w in FILLERS:
            self.totals[w] += counts[w]
            self.alerted[w] = 0

        tokens = []                                   # this sentence, word by word
        for w in res.get("result", []):
            # ----- check 1: a gap where Vosk heard nothing but the VAD hears voice -----
            gap = w["start"] - self.prev_end
            if gap >= MIN_GAP:
                run = self.longest_voiced_run(self.prev_end, w["start"])
                label = self.classify(self.prev_end, w["start"]) if run >= MIN_RUN else None
                log.debug("gap %.2fs before %r, voiced run %.2fs -> %s", gap, w["word"], run, label)
                if label:
                    events += self.mark_filler(label, tokens)

            # ----- check 2: a long "a"/"im" that is really an um/uh -----
            dur = w["end"] - w["start"]
            label = None
            if w["word"] in SUSPECT and dur >= MIN_DUR:
                label = self.classify(w["start"], w["end"])
                log.debug("suspect %r dur %.2fs -> %s", w["word"], dur, label)
            if label:
                events += self.mark_filler(label, tokens)      # replaces the wrong word
            else:
                tokens.append(w["word"])                       # keep the word as Vosk heard it
            self.prev_end = w["end"]

        if tokens:
            text = sentence_text(tokens)
            self.sentences.append(text)
            events.append(("sentence", text))
        return events

    def mark_filler(self, label, tokens):
        self.totals[label] += 1
        tokens.append("[um?]" if label == "filler?" else f"[{label}]")
        return [("filler", label)]


def load_vosk(path):
    from vosk import SetLogLevel
    SetLogLevel(-1)                                   # Kaldi's logging is very chatty
    return Model(str(path))


def load_whisper(name="tiny.en"):
    from faster_whisper import WhisperModel
    model = WhisperModel(name, compute_type="int8")
    model.transcribe(np.zeros(SR // 2, dtype=np.float32), language="en")    # warm up
    return model
