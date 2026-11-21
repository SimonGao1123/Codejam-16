# Audio analysis protocol (Electron ↔ Python)

While a practice session's microphone is on, the Electron app streams the raw audio to the
analysis service (`service/`). The service measures pitch and volume and runs speech-to-text with
filler-word detection, then sends back voice readings and transcript updates.

- Transport: WebSocket at **`ws://127.0.0.1:<port>/audio`**, on the same service and port as the
  webcam analysis (`/webcam`, see `webcam-protocol.md`).
- TypeScript types: `app/src/renderer/src/practice/audioProtocol.ts`. Python:
  `service/presentation_service/audio_analysis.py`. Keep all three in sync.
- Electron reconnects every second if the service isn't running, and drops audio rather than
  queueing it if the service falls behind.

## Electron → Python

**Audio**: binary messages of raw **float32 little-endian, mono, 16 kHz** samples, about 2048
samples (128 ms) each. Electron downsamples from the mic's own rate in an audio worklet.

**Control** (JSON text):

```json
{"type": "session_start"}    // new practice session: clear the transcript, counts and voice baseline
{"type": "session_end"}      // session ended: finish the sentence still being spoken
```

Voice calibration has no message of its own: the webcam side's `calibrate` message (the Calibrate
button) also starts voice calibration. Speak normally for 3 s (10 s timeout).

## Python → Electron

### `voice`: every 100 ms

```json
{"type": "voice", "pitch_hz": 182.4, "volume_dbfs": -31.2,
 "calibrated": false, "calibrating": false, "message": ""}
```

`pitch_hz` is `null` when the audio isn't voiced (silence, breath). `message` is the calibration
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

## Alerts on the webcam overlay

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
