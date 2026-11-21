"""
Script tracking: where in a prepared script is the speaker, are they going out of order, and have
they gone off topic? No GUI here (see script_compare_gui.py), so it can be tested on its own.

  1. The script is split into chunks (sentences; very short ones merged) and each chunk is embedded
     once with all-MiniLM-L6-v2.
  2. Every update, the recent speech (fillers removed) is embedded and compared with every chunk:
     score = embedding similarity, plus a bit of keyword overlap (names, numbers, terms). Two windows
     are scored and the better one counts per chunk: the last ~30 words (stable) and the last ~12
     (what's being said right now; without it the position lags about one sentence behind).
  3. Position tracking prefers staying put or moving forward: chunks near the expected next spot get
     a small bonus, jumps get a penalty, and chunks within `tolerance` of the best score count as
     tied (the one in the expected order wins). The position only moves when the same chunk wins
     `confirm` updates in a row, so one noisy window can't make it jump. It only ever moves
     FORWARD: going back to an earlier chunk gives a warning but keeps the position, so an
     accidental step back (or a repeated idea) doesn't lose your place.
  4. Events: jumped ahead (with the skipped chunks), went back, off topic (low match to every chunk
     AND to the script as a whole for `off_topic_s` seconds), stuck (still talking on the same chunk
     well past the time it takes to say it), or ad-libbing (on topic, but not any one chunk).

Timing uses `now` (seconds, monotonic) passed to update(), so callers and tests control the clock.
The same file is used by the analysis service: service/presentation_service/script_compare.py.
"""

import os
import re
import time
from dataclasses import dataclass, field

import numpy as np

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CACHE = os.path.expanduser("~/.cache/fastembed")   # fastembed's default is the temp folder (cleared)
WINDOW_WORDS = 30           # how much recent speech is compared with the script...
SHORT_WINDOW_WORDS = 12     # ...plus just the last few words, so the current sentence registers early
MIN_CHUNK_WORDS = 6         # shorter sentences are merged into the next one
KEYWORD_WEIGHT = 0.3        # score = 0.7 * embedding similarity + 0.3 * keyword overlap
NEXT_BONUS = 0.05           # bonus for the current chunk and the next two
JUMP_PENALTY = 0.02         # per chunk jumped forward (backwards counts double), capped below
MAX_PENALTY = 0.15
CONFIRM = 2                 # updates in a row a new chunk must win before the position moves
OFF_TOPIC_S = 6.0           # seconds below the threshold before "off topic" fires
STUCK_FACTOR = 3.0          # "stuck" after this many times a chunk's expected speaking time...
STUCK_MIN_S = 20.0          # ...but never sooner than this
WORDS_PER_S = 2.5           # typical speaking rate, for a chunk's expected speaking time

FILLERS = {"um", "uh", "umm", "uhh", "er", "erm", "ah", "hmm", "like", "so", "basically", "actually"}
STOPWORDS = set("""a an the and or but if then than that this these those is are was were be been
being to of in on at by for with from as it its it's i me my we our you your he she they them their
there here what which who whom when where why how not no yes do does did done have has had will would
can could should shall may might must just very really also about into over under again more most
some any all each other such only own same too out up down off let lets let's going gonna want""".split())


def words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def chunk_script(text):
    """Split a script into sentence chunks, merging sentences shorter than MIN_CHUNK_WORDS."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n\s*\n", text) if s.strip()]
    chunks, pending = [], ""
    for s in sentences:
        pending = f"{pending} {s}".strip()
        if len(words(pending)) >= MIN_CHUNK_WORDS:
            chunks.append(pending)
            pending = ""
    if pending:
        if chunks and len(words(pending)) < MIN_CHUNK_WORDS:
            chunks[-1] = f"{chunks[-1]} {pending}"
        else:
            chunks.append(pending)
    return chunks


def keywords(text):
    """Distinctive words: not stopwords or fillers, at least 3 letters (or any number)."""
    return {w for w in words(text) if (len(w) >= 3 or w.isdigit()) and w not in STOPWORDS and w not in FILLERS}


def speech_window(text, n=WINDOW_WORDS):
    """The last n spoken words with filler words removed."""
    return " ".join([w for w in words(text) if w not in FILLERS][-n:])


class Embedder:
    def __init__(self, model=MODEL):
        from fastembed import TextEmbedding
        self.model = TextEmbedding(model, cache_dir=CACHE)

    def __call__(self, texts):
        """Unit-length embeddings, one row per text."""
        v = np.array(list(self.model.embed(list(texts))), dtype=np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


@dataclass
class Match:
    """Result of one update."""
    window: str
    similarity: np.ndarray          # embedding similarity per chunk
    keyword: np.ndarray             # keyword overlap per chunk (0-1)
    score: np.ndarray               # combined score per chunk
    best: int                       # chunk with the highest combined score (raw, before position bias)
    chosen: int                     # chunk chosen after position bias + tolerance (the likely one)
    topic: float                    # similarity to the script as a whole
    position: int                   # tracked position after this update (-1 = not started)
    status: str                     # on track / jumped ahead / went back / ad-libbing / off topic / ...
    events: list = field(default_factory=list)      # alerts that fired on this update, see event()


def event(kind, message, cue):
    """kind: jump / back / off_topic / stuck; message: full text for logs (with chunk numbers);
    cue: short pop-up text (no chunk numbers: mid-talk, "part 7" means nothing to the speaker)."""
    return {"kind": kind, "message": message, "cue": cue}


class ScriptTracker:
    def __init__(self, chunks, embed, threshold=0.35, tolerance=0.05, off_topic_s=OFF_TOPIC_S,
                 stuck_factor=STUCK_FACTOR):
        self.chunks = chunks
        self.embed = embed
        self.threshold = threshold      # best score below this = not matching any chunk
        self.tolerance = tolerance      # chunks within this of the best score count as tied
        self.off_topic_s = off_topic_s
        self.stuck_factor = stuck_factor
        self.chunk_vecs = embed(chunks)
        self.topic_vec = embed([" ".join(chunks)])[0]
        self.chunk_keywords = [keywords(c) for c in chunks]
        self.reset()

    def reset(self):
        self.position = -1              # not started
        self.visited = set()
        self.skipped = set()
        self.candidate, self.candidate_count = None, 0
        self.off_since = None           # when the current stretch below the threshold began
        self.off_alerted = False
        self.entered_at = None          # when the current position was reached
        self.stuck_alerted = False
        self.back_warned = False        # warned about going back; reset once forward again

    def stuck_after(self, i):
        """Seconds of talking on chunk i before it counts as stuck."""
        return max(STUCK_MIN_S, self.stuck_factor * len(words(self.chunks[i])) / WORDS_PER_S)

    def bias(self, i):
        """Bonus for the expected next chunks, penalty for jumps (backwards jumps cost double)."""
        if self.position < 0:
            return NEXT_BONUS if i <= 1 else -min(JUMP_PENALTY * i, MAX_PENALTY)
        d = i - self.position
        if 0 <= d <= 2:
            return NEXT_BONUS
        return -min(JUMP_PENALTY * (abs(d) if d > 0 else 2 * abs(d)), MAX_PENALTY)

    def catch_up(self, transcript, now=None, step=8):
        """Tracking started partway through (e.g. the model finished loading mid-session): replay
        what was already said a few words at a time, so earlier parts count as visited instead of
        skipped. Alerts from the replay are dropped."""
        now = time.monotonic() if now is None else now
        said = words(transcript)
        for k in range(step, len(said), step):
            self.update(" ".join(said[:k]), now)
        self.entered_at = now           # don't count the replay towards "stuck"

    def update(self, transcript, now=None):
        """Compare the latest speech with the script; returns a Match (None if too few words).
        Call it while the speaker is talking (when the transcript changes)."""
        now = time.monotonic() if now is None else now
        window = speech_window(transcript)
        if len(window.split()) < 4:
            return None                 # not enough speech yet
        short = speech_window(transcript, SHORT_WINDOW_WORDS)
        vecs = self.embed([window, short])
        sims = vecs @ self.chunk_vecs.T                                    # (2 windows, chunks)
        kws = np.array([[len(k & keywords(w)) / len(k) if k else 0.0 for k in self.chunk_keywords]
                        for w in (window, short)])
        scores = (1 - KEYWORD_WEIGHT) * sims + KEYWORD_WEIGHT * kws
        pick = scores.argmax(axis=0)                                       # better window per chunk
        cols = np.arange(len(self.chunks))
        sim, kw, score = sims[pick, cols], kws[pick, cols], scores[pick, cols]
        topic = float(max(vecs @ self.topic_vec))
        best = int(np.argmax(score))

        # tied = within tolerance of the best raw score; among those, position bias decides
        tied = [i for i in range(len(score)) if score[i] >= score[best] - self.tolerance]
        chosen = max(tied, key=lambda i: score[i] + self.bias(i))

        events = []
        if score[best] < self.threshold:
            self.candidate, self.candidate_count = None, 0
            if topic < self.threshold:
                status = "off topic"
                self.off_since = now if self.off_since is None else self.off_since
                if not self.off_alerted and now - self.off_since >= self.off_topic_s:
                    self.off_alerted = True
                    events.append(event("off_topic", "Off topic: what you're saying doesn't match the script",
                                        "Back to your script"))
            else:
                status = "ad-libbing (on topic, not a script line)"
                self.off_since, self.off_alerted = None, False
            return Match(window, sim, kw, score, best, chosen, topic, self.position, status, events)

        self.off_since, self.off_alerted = None, False
        status = "on track" if chosen in (self.position, self.position + 1, self.position + 2) else "checking"
        if chosen == self.position:
            self.candidate, self.candidate_count = None, 0
            self.back_warned = False
        else:
            self.candidate_count = self.candidate_count + 1 if chosen == self.candidate else 1
            self.candidate = chosen
            # the next chunk only needs one update; anything else must win CONFIRM in a row
            needed = 1 if chosen == self.position + 1 else CONFIRM
            if self.candidate_count >= needed:
                if chosen > self.position:
                    events, status = self.move_to(chosen, now)
                else:
                    # going back: warn once, but keep the position (forward only)
                    status = "went back"
                    if not self.back_warned:
                        self.back_warned = True
                        events.append(event(
                            "back", f"Went back to chunk {chosen + 1} (staying at chunk {self.position + 1})",
                            "Went back"))
            elif chosen < self.position:
                status = "went back"

        # stuck: still talking on the same chunk long after it should have been said
        p = self.position
        if p >= 0 and not self.stuck_alerted and now - self.entered_at > self.stuck_after(p):
            self.stuck_alerted = True
            events.append(event("stuck", f"Stuck: still on chunk {p + 1} after {now - self.entered_at:.0f} s",
                                "Move on to the next part"))
            status = "stuck"
        return Match(window, sim, kw, score, best, chosen, topic, self.position, status, events)

    def move_to(self, i, now):
        """Move forward to chunk i (never called for earlier chunks)."""
        old = self.position
        events, status = [], "on track"
        if old < 0 and i > 1:
            skipped = [j for j in range(i) if j not in self.visited]
            self.skipped.update(skipped)
            events.append(event("jump", f"Started at chunk {i + 1}: skipped chunks {fmt(skipped)}",
                                "Skipped ahead"))
            status = "jumped ahead"
        elif i > old + 2:
            skipped = [j for j in range(old + 1, i) if j not in self.visited]
            self.skipped.update(skipped)
            events.append(event("jump", f"Jumped ahead to chunk {i + 1}: skipped {fmt(skipped)}",
                                "Skipped ahead"))
            status = "jumped ahead"
        self.position = i
        self.back_warned = False
        self.entered_at, self.stuck_alerted = now, False
        self.visited.add(i)
        self.skipped.discard(i)
        self.candidate, self.candidate_count = None, 0
        return events, status


def fmt(indexes):
    return ", ".join(str(i + 1) for i in indexes) if indexes else "none"
