# Script compare (experiment)

Paste a script, talk, and see where you are in it: a test of matching a live transcript against a
prepared script with sentence embeddings, before building it into the app.

```sh
uv run script_compare_gui.py                  # microphone
uv run script_compare_gui.py --wav talk.wav   # play a recording instead (any rate, mono or stereo)
uv run pytest -q                              # matching tests (no mic needed)
```

The first run downloads the embedding model (all-MiniLM-L6-v2 as ONNX via `fastembed`, ~90 MB, no
PyTorch). Speech-to-text uses the Vosk model in `../gap_heuristic/`.

## How it matches (`script_match.py`)

1. **Chunk** the script into sentences (merging very short ones) and embed each chunk once.
2. Every 1.5 s, embed the recent speech with fillers removed, in two windows: the last ~30 words
   (stable) and the last ~12 (what's being said now; without it the position lags ~1 sentence).
3. **Score** each chunk = 0.7 × embedding similarity + 0.3 × keyword overlap (names, numbers, terms).
4. **Track position** instead of taking the best match each time: the current and next two chunks
   get a small bonus, jumps a penalty (backwards double), and the position only moves when a chunk
   wins 2 updates in a row (1 for the very next chunk).
5. **Alerts**
   - *Jumped ahead*: moved more than 2 chunks forward; the chunks in between are marked skipped
   - *Went back*: an earlier chunk keeps matching. Warned once, but the position **never moves
     backwards**, so an accidental step back or a repeated idea doesn't lose your place
   - *Off topic*: best chunk score below the threshold **and** low similarity to the whole script,
     for `off_topic_s` (6 s) in a row. Low chunk score but on-topic = "ad-libbing", no alert.
   - *Stuck*: still talking on the same chunk after `stuck_factor` (3) × its expected speaking
     time (words / 2.5 per second), and at least 20 s. Pauses don't count (no new words, no check).

The app's service uses the same file: `service/presentation_service/script_compare.py` (copy it
over after changing this one).

## GUI

- **Chunks:** each with a score bar, the embedding and keyword parts, and its gap from the best
  match. Green = current position, yellow = best match right now, blue = tied (within tolerance),
  struck through = skipped.
- **Off-script threshold** slider: the best chunk score needed to count as on script
  (word-for-word reading scores ~0.9, a paraphrase ~0.5-0.7, unrelated speech ~0.1).
- **Match tolerance** slider: chunks within this much of the best score count as tied, and the one
  in the expected order wins. Raise it if a script repeats the same idea in several places.
- **Stuck after** slider: how many times a part's expected speaking time before "stuck".
- **Type instead of speaking**: test without a mic.
