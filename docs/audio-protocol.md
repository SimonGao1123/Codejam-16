# Audio analysis protocol (Electron ↔ Python)

While a practice session's microphone is on, the Electron app streams the raw audio to the
analysis service (`service/`). The service measures pitch and volume and runs speech-to-text with
filler-word detection, then sends back voice readings and transcript updates.

- Transport: WebSocket at **`ws://127.0.0.1:<port>/audio`**, on the same service and port as the
  webcam analysis (`/webcam`, see `webcam-protocol.md`).
- TypeScript types: `app/src/renderer/src/practice/audio/audioProtocol.ts`. Python:
  `service/presentation_service/audio_analysis.py`. Keep all three in sync.
- Electron reconnects every second if the service isn't running, and drops audio rather than
  queueing it if the service falls behind.

## Electron → Python

**Audio**: binary messages with a little-endian float64 chunk-start timestamp followed by
**float32 little-endian, mono, 16 kHz** samples, about 2048 samples (128 ms) each.
Timestamps use the renderer's monotonic clock in seconds. Electron downsamples from the mic's
own rate in an audio worklet. Clients that omit `started_at` send raw samples with no prefix.

**Control** (JSON text):

```json
{"type": "session_start", "started_at": 12.3} // recording start on the renderer's monotonic clock
{"type": "session_end"}      // finish the sentence still being spoken and return the session score
```

```json
{"type": "script", "text": "Good morning everyone..." | null}   // the user's script; null/empty = off
```

Sent when the connection opens, when the script changes (once typing pauses), and before
`session_start` if it changed. The service keeps it across sessions until it changes.

Voice calibration has no message of its own: the webcam side's `calibrate` message (the Calibrate
button) also starts voice calibration. Speak normally for 3 s (10 s timeout).

The app sends both controls for camera-only practice too. Raw audio is optional.

## Python → Electron

### `session_score`: after the final transcript

Contains `score` (out of `max_score`, currently 20, or null without measured data),
`measured_points` (the available portion of the rubric), `categories` (filler, voice tone, and
body language breakdowns), `feedback` (tips ordered by deduction), and `checkpoints` (mistakes
with a `category`, `label`, and `at` in seconds from recording start). Voice incidents come from the existing voice detector.
Webcam incidents use the configured hold durations and count each sustained condition once.
Hiding live banners does not turn off scoring.

### `voice`: every 100 ms

```json
{"type": "voice", "pitch_hz": 182.4, "pitches": [179.8, 182.4], "volume_dbfs": -31.2,
 "typical_hz": 168.0, "spike_hz": 282.5,
 "calibrated": false, "calibrating": false, "message": ""}
```

`captured_at` gives the latest reading's time on the renderer's monotonic clock, or `null`
when audio arrives without timestamps. `pitch_hz` is the latest reading, `null` when the audio isn't voiced (silence, breath, s/t/k
sounds). `pitches` holds every 50 ms reading since the last message, oldest first; the Voice pitch
graph plots these. `typical_hz` is the calibrated pitch, or the running typical pitch before
calibration (`null` for the first second of speech). `spike_hz` is where a pitch-spike alert fires
(`typical_hz` raised by the Live alerts pitch threshold, in semitones). `message` is the calibration
status, such as "Voice: keep talking normally (1.5 / 3 s)" or "Voice calibrated: 180 Hz, -30 dBFS".

### `transcript`: whenever the transcript changes

```json
{
  "type": "transcript",
  "new_sentences": ["So I think [um] the results are really good."],
  "partial": "the main idea is",
  "new_fillers": ["um"],
  "fillers": {"um": 1, "uh": 0, "like": 0, "you know": 0, "so": 1, "filler?": 0},
  "total": 2
}
```

| Field | Meaning |
|---|---|
| `new_sentences` | Finished sentences to **append**. A sentence ends when Vosk hears a pause |
| `partial` | Words of the sentence still being spoken; **replaces** the previous partial |
| `new_fillers` | Fillers detected since the last message (Electron flashes the counter) |
| `fillers`, `total` | Running counts for the session, including fillers already heard in the current partial |

Filler markers inside sentences: `[um]` / `[uh]` = detected and confirmed by Whisper; `[um?]` =
sounded like a filler but Whisper wasn't available to confirm (counted as `filler?`).

### `script_loaded`: reply to `script` (and again once the comparison model has loaded)

```json
{"type": "script_loaded", "chunks": ["Good morning everyone...", "..."], "ready": true}
```

`chunks` is the script split into parts (sentences, very short ones merged); `[]` = no script.
`ready` is false while the comparison model is still loading.

### `script_position`: at most every 1.5 s while new words arrive (only with a script)

```json
{"type": "script_position", "position": 6, "best": 6, "status": "jumped ahead",
 "skipped": [3, 4, 5], "best_score": 0.82,
 "events": ["Jumped ahead to chunk 7: skipped 4, 5, 6"]}
```

Parts are 0-based (`position` 6 = part 7; -1 = not started). `status`: on track / checking /
jumped ahead / went back / stuck / ad-libbing (on topic, not a script line) / off topic.

**How it works:** `script_compare.py` (same code as `experiments/script_compare/script_match.py`)
compares the transcript the service already builds — no extra audio processing — with the script:
all-MiniLM-L6-v2 embeddings (cosine similarity) + keyword overlap, position tracking that prefers
moving forward, and events for skipping ahead, going back, going off topic and getting stuck.
The position only moves **forward**: going back to an earlier part warns once ("Went back") but
keeps your place, so `status` is `went back` while `position` stays put (`best` shows the part
being matched).
If tracking starts after the speaker did (model still loading), it first replays what was already
said so earlier parts count as visited.

## Alerts on the webcam overlay

Script pop-ups (no part numbers; mid-talk they mean nothing): `Skipped ahead` / `Went back` (rule
`out_of_order`), `Back to your script` (rule `off_script`: threshold = "% match needed", duration =
seconds off script before it fires) and `Move on to the next part` (rule `stuck`: threshold = how
many times a part's expected speaking time, at least 20 s).

The Live alerts tab's `volume`, `pitch`, `quiet` and `filler` rules (sent in the webcam side's
`settings` message) control these: on/off, threshold (dB above / semitones above / dB below
normal), minimum duration and cooldown. Fillers are always counted in the transcript; the rule
only controls the pop-up.

The audio side posts alerts that the webcam side adds to its `alerts` banners for 2.5 s:
`Speak up` (too quiet or paused too long), `Steady your volume`, `Steady your pitch`, and
`Filler: um` (etc.) each time a filler is detected. The current voice reading also appears as a
status line (`voice: 182 Hz  -31 dBFS`).

## How the transcript and fillers work

From `experiments/gap_heuristic/SpeechtoText.py`:

1. **Vosk** (`experiments/gap_heuristic/vosk-model-small-en-us-0.15`) produces the transcript and
   counts the filler words it writes: um, uh, like, you know, so.
2. Vosk often drops um/uh, so two checks look for them:
   - a **gap** between words where the webrtcvad still heard voice
   - a long **near-miss word** ("a", "im", "ah", "huh", ...) that is probably a filler
3. Each suspicious clip gets a second opinion from **Whisper** `tiny.en`, prompted to write fillers.
   It's downloaded on first run (~75 MB); without it, suspicious clips become `[um?]`.
