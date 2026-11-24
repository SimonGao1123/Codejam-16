"""
Live transcript with filler-word detection and speaking pace, taking audio pushed in from the
Electron app instead of reading the mic. Based on the current Moonshine script:

  - Moonshine streams the transcript (partial text while you talk, a finished line on a pause).
    It writes "um" and "uh" itself, nudged by key-term biasing, so fillers are found straight in its
    text (fillers.py). Ones it drops again when it finishes a line are put back.
  - Alerts fire from partial text for speed. Totals come from finished lines only,
    because partials get revised as more audio arrives.
  - Pace: Moonshine's streaming model gives no per-word timings, but each finished line has a
    start time and duration, and lines end at pauses. So a line's words are spread evenly over its
    duration (`word_times`, used for the final score). The live WPM also counts the words of the
    line still being spoken, and webrtcvad tells it when the speaker has paused.

Interface: feed() / flush() / reset(), returning events like ("partial", text),
("sentence", text with fillers in [brackets]), ("filler", word); plus word_times, pace_history and live_wpm() for pace.
All times are seconds of session audio (the audio clock), like Moonshine's line start times.
"""

import logging
import re
from collections import deque

import numpy as np
import webrtcvad
from moonshine_voice import Transcriber as MoonshineTranscriber
from moonshine_voice import TranscriptEventListener, get_model_for_language

from .fillers import FILLERS, LineHesitations, find_fillers

log = logging.getLogger(__name__)

# ---------- settings ----------
SR = 16000
FRAME = 480                  # 30 ms: the frame size audio_analysis.py feeds in (and webrtcvad accepts)
FRAME_SEC = FRAME / SR       # 0.03 s
CHUNK = 1600                 # 0.1 s: frames are batched to this size before Moonshine
UPDATE_INTERVAL = 0.5        # how often Moonshine refreshes its partial text (seconds)
KEYTERMS = ["um", "uh"]      # Moonshine is biased towards writing these; it tends to leave them out...
KEYTERM_BOOST = 1.5          # ...higher finds more of them but also makes more up (benchmarks/README.md)
VAD_LEVEL = 2                # 0-3, higher = stricter about what counts as speech
LIVE_WPM_WINDOW_S = 12       # short enough to react, long enough not to jump word-by-word
LIVE_WPM_PAUSE_S = 0.8       # show N/A promptly when the speaker has paused

END_PUNCT = (".", ",", "?", "!")     # don't add a period after any of these


def count_fillers(text, unfinished=False):
    counts = {f: 0 for f in FILLERS}
    for _, _, filler in find_fillers(text, unfinished):
        counts[filler] += 1
    return counts


def sentence_text(text):
    """'so i think um we should go' -> 'So I think um we should go.'"""
    text = text.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]                  # capitalize the first letter only
    if not text.endswith(END_PUNCT):                   # Moonshine sometimes adds punctuation already
        text += "."
    return re.sub(r"\bi\b", "I", text)                 # lone "i" -> "I" (also i'm, i'll)


def mark_fillers(sentence):
    """'So I think um it works.' -> '[So] I think [um] it works.' (the app highlights the bracketed words)"""
    for start, end, _ in sorted(find_fillers(sentence), reverse=True):
        sentence = f"{sentence[:start]}[{sentence[start:end]}]{sentence[end:]}"
    return sentence


def spoken_words(text):
    """Words for pace counting: punctuation-only tokens don't count."""
    return re.findall(r"[A-Za-z0-9']+", text)


class _Listener(TranscriptEventListener):
    """Forwards Moonshine's callbacks to the Transcriber that owns the stream."""

    def __init__(self, owner):
        self.owner = owner

    def on_line_text_changed(self, event):
        self.owner._on_partial(event.line.text)

    def on_line_completed(self, event):
        line = event.line
        self.owner._on_final(line.text, getattr(line, "start_time", 0.0), getattr(line, "duration", 0.0))


class Transcriber:
    """Feed 30 ms frames of 16 kHz int16 audio; returns transcript events as they happen."""

    def __init__(self, model):
        """model: what load_moonshine() returns."""
        self.model = model
        self.stream = None
        self.vad = webrtcvad.Vad(VAD_LEVEL)
        self.reset()

    def reset(self):
        self.close()
        self.buf = np.zeros(0, dtype=np.float32)      # audio waiting to be sent in CHUNK-sized pieces
        self.pending = deque()                        # events from Moonshine's callbacks, drained by feed()
        self.sentences = []                           # every finished sentence, as text
        self.totals = {f: 0 for f in FILLERS}
        self.filler_checkpoints = []                  # (estimated time, filler) for each filler counted
        self.alerted = {f: 0 for f in FILLERS}        # fillers already alerted from this line's partials
        self.last_partial = ""
        self.hesitations = LineHesitations()          # um / uh in this line's partials
        # pace
        self.frame_count = 0                          # audio clock: frame_count * FRAME_SEC seconds
        self.word_times = []                          # (start, end) per finished word (estimated in-line)
        self.lines = []                               # (start, end, words) per finished line (ends at a pause)
        self.pace_history = deque()                   # (audio time, words so far incl. the partial line)
        self.last_voiced_at = None                    # audio time of the last frame with speech
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

    # ---------- callbacks from Moonshine ----------
    def _new_fillers(self, counts):
        """Fillers in `counts` beyond what was already alerted for this line (one per occurrence)."""
        hits = []
        for word, n in counts.items():
            while self.alerted[word] < n:
                self.alerted[word] += 1
                hits.append(word)
        return hits

    def _on_partial(self, text):
        if text == self.last_partial:
            return
        self.last_partial = text
        self.hesitations.add_partial(text)
        self.pending.append(("partial", text))
        for w in self._new_fillers(count_fillers(text, unfinished=True)):
            self.pending.append(("filler", w))

    def _on_final(self, text, start_time, duration=0.0):
        text = self.hesitations.restore(text)
        self.hesitations = LineHesitations()
        counts = count_fillers(text)
        for w in self._new_fillers(counts):            # ones the partials didn't show
            self.pending.append(("filler", w))
        for w in FILLERS:
            self.totals[w] += counts[w]
            self.alerted[w] = 0                        # reset for the next line

        # no per-word timings from the streaming model: spread the line's words over its duration
        words = spoken_words(text)
        if words and duration > 0:
            step = duration / len(words)
            self.word_times += [(start_time + i * step, start_time + (i + 1) * step)
                                for i in range(len(words))]
            self.lines.append((start_time, start_time + duration, len(words)))
        for at, _, w in find_fillers(text):
            said = len(spoken_words(text[:at]))        # words of the line before this filler
            self.filler_checkpoints.append((start_time + duration * said / len(words), w))

        sentence = sentence_text(text)
        if sentence:
            self.sentences.append(sentence)
            self.pending.append(("sentence", mark_fillers(sentence)))
        self.last_partial = ""
        self.pending.append(("partial", ""))           # clear the partial line on screen
        log.debug("line completed: %r (%.2fs + %.2fs)", text, start_time, duration)

    # ---------- main ----------
    def drain(self):
        events = []
        while self.pending:
            events.append(self.pending.popleft())
        return events

    def feed(self, frame):
        """One 30 ms frame (480 int16 samples as bytes) -> list of events."""
        self.frame_count += 1
        if len(frame) == FRAME * 2 and self.vad.is_speech(frame, SR):
            self.last_voiced_at = self.frame_count * FRAME_SEC
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        self.buf = np.concatenate([self.buf, samples])
        while len(self.buf) >= CHUNK:
            chunk, self.buf = self.buf[:CHUNK], self.buf[CHUNK:]
            self.stream.add_audio(chunk, SR)
        events = self.drain()
        self._record_live_pace()
        return events

    def flush(self):
        """End of the session: stop the stream so the open line finishes, then return its events."""
        if self.stream is not None:
            self.stream.stop()
        return self.drain()

    # ---------- pace ----------
    def _record_live_pace(self):
        """Remember how many words have been said (finished + partial line) at this audio time."""
        now = self.frame_count * FRAME_SEC
        words = len(self.word_times) + len(spoken_words(self.last_partial))
        self.pace_history.append((now, words))
        while self.pace_history and self.pace_history[0][0] < now - LIVE_WPM_WINDOW_S - 1:
            self.pace_history.popleft()

    def live_wpm(self, minimum_seconds=4, window_seconds=LIVE_WPM_WINDOW_S):
        """Smoothed, provisional WPM from the partial transcript; None while paused or until there's
        enough speech to judge."""
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


def load_moonshine(language="en"):
    """Load the Moonshine model once (downloads and caches it the first time)."""
    model_path, model_arch = get_model_for_language(language)
    # by default every update hands back the audio of the whole session so far, as Python lists of
    # floats: after a few minutes that takes longer than the speech recognition itself
    model = MoonshineTranscriber(model_path=model_path, model_arch=model_arch,
                                 options={"return_audio_data": "false", "keyterm_boost": str(KEYTERM_BOOST)})
    model.set_keyterms(KEYTERMS)
    return model
