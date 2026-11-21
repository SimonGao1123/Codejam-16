"""
Live transcript with filler-word detection, restructured to take audio pushed in from the
Electron app instead of reading the mic. Based on the current Moonshine script:

  - Moonshine streams the transcript (partial text while you talk, a finished line on a pause).
    It writes "um" and "uh" itself, so fillers are counted straight from its text.
  - Alerts fire from partial text for speed. Totals come from finished lines only,
    because partials get revised as more audio arrives.
  - Optional: pass a noise profile (a couple of seconds of room audio) and each chunk is cleaned
    with noisereduce before it reaches Moonshine.

Interface is the same as before: feed() / flush() / reset(), returning events like
("partial", text), ("sentence", text), ("filler", word).
"""

import logging
import re
from collections import deque

import numpy as np
from moonshine_voice import Transcriber as MoonshineTranscriber
from moonshine_voice import TranscriptEventListener, get_model_for_language

log = logging.getLogger(__name__)

# ---------- settings ----------
SR = 16000
FRAME = 480                  # 480 samples = 30 ms (the size the VAD accepts)
FRAME_SEC = FRAME / SR       # 0.03 s
MIN_GAP = 0.25               # only look at gaps at least this long (seconds)
MIN_RUN = 0.15               # continuous voiced sound inside a gap = suspicious
VAD_LEVEL = 2                # 0-3, higher = stricter about what counts as speech
KEEP_SECONDS = 60            # how much history (voiced + audio) to keep
MAX_FRAMES = int(KEEP_SECONDS / FRAME_SEC)
LIVE_WPM_WINDOW_S = 12       # short enough to react, long enough not to jump word-by-word
LIVE_WPM_PAUSE_S = 0.8        # show N/A promptly when the speaker has paused

# matched against lowercase text
FILLER_PATTERNS = {
    "um": r"\bum\b",
    "uh": r"\buh\b",
    "erm": r"\berm\b",
    "hmm": r"\bhmm\b",
    "like": r"\blike\b",
    "you know": r"\byou know\b",
    "so": r"\bso\b",
}
FILLERS = list(FILLER_PATTERNS)

END_PUNCT = (".", ",", "?", "!")     # don't add a period after any of these


def count_fillers(text):
    text = text.lower()
    return {f: len(re.findall(p, text)) for f, p in FILLER_PATTERNS.items()}


def sentence_text(text):
    """'so i think um we should go' -> 'So I think um we should go.'"""
    text = text.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]                  # capitalize the first letter only
    if not text.endswith(END_PUNCT):                   # Moonshine sometimes adds punctuation already
        text += "."
    return re.sub(r"\bi\b", "I", text)                 # lone "i" -> "I" (also i'm, i'll)


class _Listener(TranscriptEventListener):
    """Forwards Moonshine's callbacks to the Transcriber that owns the stream."""

    def __init__(self, owner):
        self.owner = owner

    def on_line_text_changed(self, event):
        self.owner._on_partial(event.line.text)

    def on_line_completed(self, event):
        self.owner._on_final(event.line.text, getattr(event.line, "start_time", 0.0))


class Transcriber:
    """Feed 30 ms frames of 16 kHz int16 audio; returns transcript events as they happen."""

    def __init__(self, model, noise=None):
        """model: what load_moonshine() returns.
        noise: optional float32 array of room noise at 16 kHz (about 2 s) to clean audio with."""
        self.model = model
        self.noise = noise
        self.stream = None
        self.nr = None
        if noise is not None:
            import noisereduce
            self.nr = noisereduce
        self.reset()

    def reset(self):
        self.close()
        self.buf = np.zeros(0, dtype=np.float32)      # audio waiting to be sent in CHUNK-sized pieces
        self.pending = deque()                        # events from Moonshine's callbacks, drained by feed()
        self.sentences = []                           # every finished sentence, as text
        self.totals = {f: 0 for f in FILLERS}
        self.filler_checkpoints = []                  # (line start time, filler) for each filler counted
        self.alerted = {f: 0 for f in FILLERS}        # fillers already alerted from this line's partials
        self.last_partial = ""
        self.stream = self.model.create_stream(update_interval=UPDATE_INTERVAL)
        self.stream.add_listener(_Listener(self))
        self.stream.start()

    def close(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                log.debug("stream was already stopped", exc_info=True)
            self.stream = None

    @property
    def paragraph(self):
        return " ".join(self.sentences)

    # ---------- callbacks from Moonshine (may run on another thread) ----------
    def _new_fillers(self, counts):
        """Fillers in `counts` beyond what was already alerted for this line (one per occurrence)."""
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
        is_voiced = self.vad.is_speech(frame, SR)
        self.voiced.append(is_voiced)
        if is_voiced:
            self.last_voiced_at = self.frame_count * FRAME_SEC
        if len(self.voiced) > MAX_FRAMES:
            self.voiced.popleft()
            self.audio.popleft()                 # trim together so frame numbers match
            self.base += 1

        if self.recognizer.AcceptWaveform(frame):
            events = self.finish_phrase(json.loads(self.recognizer.Result()))
            self.last_partial = ""
            events += [("partial", "")]
        else:
            partial = json.loads(self.recognizer.PartialResult()).get("partial", "")
            if partial == self.last_partial:
                events = []
            else:
                self.last_partial = partial
                events = [("partial", partial)] + [
                    ("filler", w) for w in self.new_fillers(count_fillers(partial))
                ]
        self._record_live_pace()
        return events

    def _record_live_pace(self):
        """Remember the partial transcript count at this audio-clock position."""
        now = self.frame_count * FRAME_SEC
        words = len(self.word_times) + len(self.last_partial.split())
        self.pace_history.append((now, words))
        while self.pace_history and self.pace_history[0][0] < now - LIVE_WPM_WINDOW_S - 1:
            self.pace_history.popleft()

    def live_wpm(self, minimum_seconds=4, window_seconds=LIVE_WPM_WINDOW_S):
        """Smoothed, provisional WPM from Vosk partials; None until it has enough context."""
        if not self.pace_history:
            return None
        now, words_now = self.pace_history[-1]
        if self.last_voiced_at is None or now - self.last_voiced_at > LIVE_WPM_PAUSE_S:
            return None
        start = next((sample for sample in self.pace_history if sample[0] >= now - window_seconds),
                     self.pace_history[0])
        elapsed = now - start[0]
        word_change = words_now - start[1]
        if elapsed < minimum_seconds or word_change < 3:
            return None
        return round(max(0, word_change) * 60 / elapsed, 1)
    def _on_partial(self, text):
        if text == self.last_partial:
            return
        self.last_partial = text
        self.pending.append(("partial", text))
        for w in self._new_fillers(count_fillers(text)):
            self.pending.append(("filler", w))

    def _on_final(self, text, start_time):
        counts = count_fillers(text)
        for w in self._new_fillers(counts):            # ones the partials didn't show
            self.pending.append(("filler", w))
        for w in FILLERS:
            self.totals[w] += counts[w]
            self.filler_checkpoints += [(start_time, w)] * counts[w]
            self.alerted[w] = 0                        # reset for the next line

        sentence = sentence_text(text)
        if sentence:
            self.sentences.append(sentence)
            self.pending.append(("sentence", sentence))
        self.last_partial = ""
        self.pending.append(("partial", ""))           # clear the partial line on screen
        log.debug("line completed: %r", text)

    # ---------- main ----------
    def drain(self):
        events = []
        counts = count_fillers(res.get("text", ""))
        events += [("filler", w) for w in self.new_fillers(counts)]     # ones the partials didn't show
        for w in FILLERS:
            self.totals[w] += counts[w]
            self.alerted[w] = 0

        tokens = []                                   # this sentence, word by word
        words = res.get("result", [])
        for index, w in enumerate(words):
            self.word_times.append((float(w["start"]), float(w["end"])))
            label = w["word"]
            if label == "you" and index + 1 < len(words) and words[index + 1]["word"] == "know":
                label = "you know"
            if label in FILLERS:
                self.filler_checkpoints.append((w["start"], label))
            # ----- check 1: a gap where Vosk heard nothing but the VAD hears voice -----
            gap = w["start"] - self.prev_end
            if gap >= MIN_GAP:
                run_start, run = self.longest_voiced_run(self.prev_end, w["start"])
                label = self.classify(self.prev_end, w["start"]) if run >= MIN_RUN else None
                log.debug("gap %.2fs before %r, voiced run %.2fs -> %s", gap, w["word"], run, label)
                if label:
                    events += self.mark_filler(label, tokens, run_start)

            # ----- check 2: a long "a"/"im" that is really an um/uh -----
            dur = w["end"] - w["start"]
            label = None
            if w["word"] in SUSPECT and dur >= MIN_DUR:
                label = self.classify(w["start"], w["end"])
                log.debug("suspect %r dur %.2fs -> %s", w["word"], dur, label)
            if label:
                events += self.mark_filler(label, tokens, w["start"])  # replaces the wrong word
            else:
                tokens.append(w["word"])                       # keep the word as Vosk heard it
            self.prev_end = w["end"]

        if tokens:
            text = sentence_text(tokens)
            self.sentences.append(text)
            events.append(("sentence", text))
        while self.pending:
            events.append(self.pending.popleft())
        return events

    def feed(self, frame):
        """One 30 ms frame (480 int16 samples as bytes) -> list of events."""
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        self.buf = np.concatenate([self.buf, samples])
        while len(self.buf) >= CHUNK:
            chunk, self.buf = self.buf[:CHUNK], self.buf[CHUNK:]
            if self.nr is not None:
                chunk = self.nr.reduce_noise(
                    y=chunk, sr=SR, y_noise=self.noise, stationary=True
                ).astype(np.float32)
            self.stream.add_audio(chunk, SR)
        return self.drain()

    def flush(self):
        """End of the session: stop the stream so the open line finishes, then return its events."""
        if self.stream is not None:
            self.stream.stop()
        return self.drain()


def load_moonshine(language="en"):
    """Load the Moonshine model once (downloads and caches it the first time)."""
    model_path, model_arch = get_model_for_language(language)
    return MoonshineTranscriber(model_path=model_path, model_arch=model_arch)