"""
Script tracking: where in a prepared script is the speaker, are they going out of order, and have
they gone off topic? No GUI here (see script_compare_gui.py), so it can be tested on its own.

  1. The script is split into chunks (sentences; very short ones merged) and each chunk is embedded
     once with all-MiniLM-L6-v2.
  2. Every update, the recent speech (fillers removed) is embedded and compared with every chunk:
     score = embedding similarity, plus a bit of keyword overlap (names, numbers, terms). Two windows
     are scored and the better one counts per chunk: the last ~30 words (stable) and the last ~12
     (what's being said right now; without it the position lags about one sentence behind).
     That score says whether the speaker is on script. Where they are comes from a second comparison: the
     last ~12 words against every 12-word window of the script (one ending every few words), so a window
     that runs from the end of one sentence into the next matches the place it really is.
  3. Position tracking prefers staying put or moving forward: chunks near the expected next spot get
     a small bonus, jumps get a penalty, and chunks within `tolerance` of the best score count as
     tied (the one in the expected order wins). The position only moves when the same chunk wins
     `confirm` updates in a row, so one noisy window can't make it jump. It only ever moves
     FORWARD: going back to an earlier chunk gives a warning but keeps the position, so an
     accidental step back (or a repeated idea) doesn't lose your place.
  4. Events: jumped ahead (every jump past the next chunk, with the skipped chunks), went back, off
     script (what's said matches no chunk for `off_topic_s` seconds, whether or not it's on the
     script's topic), stuck (still talking on the same chunk well past the time it takes to say it).
     The status still tells "off topic" from "ad-libbing" (on topic, but not any one chunk).

Timing uses `now` (seconds, monotonic) passed to update(), so callers and tests control the clock.
The same file is used by the analysis service: service/presentation_service/script_compare.py.
"""

import os
import re
import time
from dataclasses import dataclass, field

import numpy as np

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384
EMBED_BATCH = 16
EMBED_THREADS = 2           # as fast as more for a few short texts, and leaves the cores to Moonshine and MediaPipe
CACHE = os.path.expanduser("~/.cache/fastembed")   # fastembed's default is the temp folder (cleared)
WINDOW_WORDS = 30           # how much recent speech is compared with the script...
SHORT_WINDOW_WORDS = 12     # ...plus just the last few words, so the current sentence registers early
HEARD_WORDS = 6             # moving on to the next chunk needs HEARD_MIN words or word pairs of its own in
HEARD_MIN = 2               # the last HEARD_WORDS words
PLACE_STEP = 6              # the script is also cut into 12-word windows, one ending every 6 words
MIN_CHUNK_WORDS = 6         # shorter sentences are merged into the next one
KEYWORD_WEIGHT = 0.3        # score = 0.7 * embedding similarity + 0.3 * keyword overlap
NEXT_BONUS = 0.05           # bonus for the current chunk and the next one
JUMP_PENALTY = 0.02         # per chunk jumped forward (backwards counts double), capped below
MAX_PENALTY = 0.15
CONFIRM = 2                 # updates in a row a new chunk must win before the position moves...
CONFIRM_S = 1.5             # ...over at least this long
JUMP_MIN_SCORE = 0.55       # moving more than one chunk ahead needs a confident match (reading a
                            # line scores ~0.7-0.9; improvising drifts around 0.3-0.5)
CATCH_UP_WORDS = 100        # speech before a replayed moment that's looked at (plenty for 30 without fillers)
TAIL_CHARS = 1000           # the end of the transcript that's looked at for the last 30 words
BACK_ON_SCRIPT = 2          # matching updates in a row (over CONFIRM_S) before an off-script stretch ends
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
    recent = [w for w in words(text[-TAIL_CHARS:]) if w not in FILLERS]
    if len(recent) <= n and len(text) > TAIL_CHARS:         # (the first word may be cut off)
        recent = [w for w in words(text) if w not in FILLERS]
    return " ".join(recent[-n:])


class Embedder:
    def __init__(self, model=MODEL):
        from fastembed import TextEmbedding
        self.model = TextEmbedding(model, cache_dir=CACHE, threads=EMBED_THREADS)

    def __call__(self, texts):
        """Unit-length embeddings, one row per text. Texts of similar length are run together, because
        every text in a batch is padded to the longest one."""
        texts = list(texts)
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        v = np.empty((len(texts), EMBED_DIM), dtype=np.float32)
        v[order] = list(self.model.embed([texts[i] for i in order], batch_size=EMBED_BATCH))
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


def place_windows(chunks):
    """The script cut into overlapping SHORT_WINDOW_WORDS windows -> (texts, first window of each chunk).
    A window belongs to the chunk its last word is in."""
    said, texts, starts = [], [], []
    for chunk in chunks:
        starts.append(len(texts))
        new = [w for w in words(chunk) if w not in FILLERS] or words(chunk) or [chunk]
        first = len(said)
        said += new
        ends = {*range(first, len(said), PLACE_STEP), len(said) - 1}
        texts += [" ".join(said[max(0, i + 1 - SHORT_WINDOW_WORDS):i + 1]) for i in sorted(ends)]
    return texts, starts


def pairs(ws):
    return set(zip(ws, ws[1:]))


class ScriptTracker:
    def __init__(self, chunks, embed, threshold=0.35, tolerance=0.05, off_topic_s=OFF_TOPIC_S,
                 stuck_factor=STUCK_FACTOR, known=None):
        """known: another tracker's `vectors`, so that after an edit only the changed text is embedded."""
        self.chunks = chunks
        self.embed = embed
        self.threshold = threshold      # best score below this = not matching any chunk
        self.tolerance = tolerance      # chunks within this of the best score count as tied
        self.off_topic_s = off_topic_s
        self.stuck_factor = stuck_factor
        texts, self.place_starts = place_windows(chunks)
        known = known or {}
        new = [t for t in dict.fromkeys([*chunks, *texts]) if t not in known]
        known = {**known, **(dict(zip(new, embed(new))) if new else {})}
        self.vectors = {t: known[t] for t in [*chunks, *texts]}
        self.chunk_vecs = np.array([known[c] for c in chunks])
        self.place_vecs = np.array([known[t] for t in texts])
        self.topic_vec = embed([" ".join(chunks)])[0]
        # which chunk (row) has which keyword (column), to score every chunk at once
        chunk_keywords = [keywords(c) for c in chunks]
        self.keyword_column = {w: i for i, w in enumerate(sorted(set().union(*chunk_keywords)))}
        self.keyword_rows = np.zeros((len(chunks), len(self.keyword_column)))
        for row, ws in zip(self.keyword_rows, chunk_keywords):
            row[[self.keyword_column[w] for w in ws]] = 1
        self.keyword_totals = np.maximum(self.keyword_rows.sum(axis=1), 1)
        self.place_rows = self.keyword_hits(texts)
        self.place_totals = np.maximum(self.place_rows.sum(axis=1), 1)
        self.chunk_index = np.arange(len(chunks))
        spoken = [[w for w in words(c) if w not in FILLERS] for c in chunks]
        self.chunk_keywords, self.chunk_pairs = chunk_keywords, [pairs(ws) for ws in spoken]
        self.chunk_word_counts = [len(words(c)) for c in chunks]
        self.reset()

    def reset(self):
        self.position = -1              # not started
        self.visited = set()
        self.skipped = set()
        self.candidate, self.candidate_count, self.candidate_since = None, 0, None
        self.off_since = None           # when the current stretch below the threshold began
        self.off_alerted = False
        self.entered_at = None          # when the current position was reached
        self.stuck_alerted = False
        self.back_warned = False        # warned about going back; reset once forward again
        self.matched_ahead = set()      # chunks ahead that were the clear best match since the last
                                        # move: said, even if the position then jumps past them
        self.on_count, self.on_since = 0, None      # matching updates in a row (end an off-script stretch)

    def stuck_after(self, i):
        """Seconds of talking on chunk i before it counts as stuck."""
        return max(STUCK_MIN_S, self.stuck_factor * self.chunk_word_counts[i] / WORDS_PER_S)

    def bias(self):
        """Per chunk: a bonus for the expected next chunks, a penalty for jumps (backwards jumps cost double)."""
        if self.position < 0:
            jump, expected = self.chunk_index, self.chunk_index == 0
        else:
            d = self.chunk_index - self.position
            jump, expected = np.where(d > 0, d, -2 * d), (d == 0) | (d == 1)
        return np.where(expected, NEXT_BONUS, -np.minimum(JUMP_PENALTY * jump, MAX_PENALTY))

    def catch_up(self, transcript, now=None, step=8):
        """Tracking started partway through (e.g. the model finished loading mid-session): replay
        what was already said a few words at a time, so earlier parts count as visited instead of
        skipped. Alerts from the replay are dropped."""
        now = time.monotonic() if now is None else now
        said = words(transcript)
        windows = [self.windows(" ".join(said[max(0, k - CATCH_UP_WORDS):k])) for k in range(step, len(said), step)]
        windows = [w for w in windows if w]
        scored = self.score(windows) if windows else []            # embedded together, not one by one
        for k, moment in enumerate(scored):
            self.track(moment, now - (len(scored) - k) * step / WORDS_PER_S)
        self.entered_at = now           # don't count the replay towards "stuck" or "off script"
        self.off_since = None if self.off_since is None else now

    def update(self, transcript, now=None):
        """Compare the latest speech with the script; returns a Match (None if too few words).
        Call it while the speaker is talking (when the transcript changes)."""
        now = time.monotonic() if now is None else now
        windows = self.windows(transcript)
        if windows is None:
            return None                 # not enough speech yet
        return self.track(self.score([windows])[0], now)

    def windows(self, transcript):
        """(the last ~30 words, the last ~12); None if there's too little speech."""
        window = speech_window(transcript)
        if len(window.split()) < 4:
            return None
        return window, speech_window(transcript, SHORT_WINDOW_WORDS)

    def keyword_hits(self, texts):
        """Which of the script's keywords each text has (one row per text)."""
        hits = np.zeros((len(texts), len(self.keyword_column)))
        for row, text in zip(hits, texts):
            row[[self.keyword_column[w] for w in set(text.split()) if w in self.keyword_column]] = 1
        return hits

    def score(self, windows):
        """[(window, similarity per chunk, keyword overlap per chunk, score per chunk, topic, place score
        per chunk), ...] for many moments at once: one embedding run and a few matrix products."""
        texts = [w for pair in windows for w in pair]
        vecs = self.embed(texts)
        said = self.keyword_hits(texts)
        shape = (len(windows), 2, len(self.chunks))                        # (moments, 2 windows, chunks)
        sims = (vecs @ self.chunk_vecs.T).reshape(shape)
        kws = (said @ self.keyword_rows.T / self.keyword_totals).reshape(shape)
        scores = (1 - KEYWORD_WEIGHT) * sims + KEYWORD_WEIGHT * kws
        pick = scores.argmax(axis=1)[:, None]                              # better window per chunk
        sim, kw, score = (np.take_along_axis(a, pick, axis=1)[:, 0] for a in (sims, kws, scores))
        topic = (vecs @ self.topic_vec).reshape(shape[:2]).max(axis=1)
        # the short windows (every second row) against every window of the script, best per chunk
        place = ((1 - KEYWORD_WEIGHT) * (vecs[1::2] @ self.place_vecs.T)
                 + KEYWORD_WEIGHT * (said[1::2] @ self.place_rows.T / self.place_totals))
        place = np.maximum.reduceat(place, self.place_starts, axis=1)
        return [(*w, sim[i], kw[i], score[i], float(topic[i]), place[i]) for i, w in enumerate(windows)]

    def heard(self, i, short):
        """Whether the last few words are chunk i's own (not shared with the current chunk)."""
        recent = short.split()[-HEARD_WORDS:]
        new = self.chunk_keywords[i] - self.chunk_keywords[i - 1]
        new_pairs = self.chunk_pairs[i] - self.chunk_pairs[i - 1]
        return len(new & set(recent)) + len(new_pairs & pairs(recent)) >= HEARD_MIN

    def track(self, scored, now):
        """Moves the position on from one scored moment and raises alerts -> Match."""
        window, short, sim, kw, score, topic, place = scored
        best = int(np.argmax(place))
        match = float(score.max())

        # tied = within tolerance of the best place; among those, position bias decides
        tied = place >= place[best] - self.tolerance
        bias = self.bias()
        chosen = int(np.argmax(np.where(tied, place + bias, -np.inf)))
        # the end of this chunk also sounds like a window running into the next one: only move on once
        # words of its own were heard, or it wins as a whole
        if chosen == self.position + 1 and self.position >= 0 and not self.heard(chosen, short):
            whole = np.where(score >= match - self.tolerance, score + bias, -np.inf)
            if int(np.argmax(whole)) != chosen:
                chosen = self.position

        events = []
        if match < self.threshold:
            # matches no chunk: off script, whether it's off topic or on-topic ad-libbing
            self.candidate, self.candidate_count = None, 0
            self.on_count = 0
            status = "off topic" if topic < self.threshold else "ad-libbing (on topic, not a script line)"
            self.off_since = now if self.off_since is None else self.off_since
            if not self.off_alerted and now - self.off_since >= self.off_topic_s:
                self.off_alerted = True
                events.append(event("off_topic", "Off script: what you're saying doesn't match any part",
                                    "Back to your script"))
            return Match(window, sim, kw, score, best, chosen, topic, self.position, status, events)

        self.on_since = now if self.on_count == 0 else self.on_since
        self.on_count += 1
        if self.on_count >= BACK_ON_SCRIPT and now - self.on_since >= CONFIRM_S:     # one stray match doesn't end an off-script stretch
            self.off_since, self.off_alerted = None, False
        whole_best = int(np.argmax(score))      # (as a whole: the end of this chunk places ahead too)
        if whole_best > self.position and score[whole_best] >= JUMP_MIN_SCORE:
            self.matched_ahead.add(whole_best)
        status = "on track" if chosen in (self.position, self.position + 1) else "checking"
        if chosen == self.position:
            self.candidate, self.candidate_count = None, 0
            self.back_warned = False
        else:
            if chosen == self.candidate:
                self.candidate_count += 1
            else:
                self.candidate, self.candidate_count, self.candidate_since = chosen, 1, now
            # the next chunk only needs one update; anything else must win CONFIRM in a row
            jump = chosen > self.position + 1
            confirmed = chosen == self.position + 1 or (
                self.candidate_count >= CONFIRM and now - self.candidate_since >= CONFIRM_S)
            if confirmed and (not jump or place[chosen] >= JUMP_MIN_SCORE):
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
        # chunks that clearly matched on the way count as said, not skipped
        self.visited.update(j for j in self.matched_ahead if j < i)
        if old < 0 and i > 0:
            skipped = [j for j in range(i) if j not in self.visited]
            self.skipped.update(skipped)
            events.append(event("jump", f"Started at chunk {i + 1}: skipped chunks {fmt(skipped)}",
                                "Skipped ahead"))
            status = "jumped ahead"
        elif i > old + 1:
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
        self.matched_ahead.clear()
        self.candidate, self.candidate_count = None, 0
        return events, status


def fmt(indexes):
    return ", ".join(str(i + 1) for i in indexes) if indexes else "none"
