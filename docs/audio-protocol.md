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
{"type": "session_start", "started_at": 12.3, "voice_baseline": {"pitch_hz": 168.0, "volume_dbfs": -25.0}}
{"type": "session_end"}      // finish the sentence still being spoken and return the session score
```

Uploaded recordings use `"voice_baseline": null, "recording_baseline": true`. The service uses
the median of all voiced audio to establish a recording-local pitch and volume reference, then
grades sustained changes relative to that reference. This makes
the result independent of the file's overall gain and the user's live microphone calibration.

Uploads also send `"mode": "recording"` on `session_start`. This tells the webcam analyzer to
discard any prior live-session calibration and establish its own opening posture reference. Hand
speed, busyness and repetition stay shoulder-width-relative; the upload never treats the opening
hand position as a "hands at rest" calibration.

Upload processing sends `{"type": "audio_sync", "id": 12}` immediately after each binary
chunk and waits for `{"type": "audio_processed", "id": 12}`. The reply is sent only after
the preceding ordered chunk has been processed, providing backpressure so an uploaded recording
cannot outrun the streaming transcription model.

```json
{"type": "noise_floor", "dbfs": -59.9}   // room noise, sent just before session_start
```

The app measures the room while the user stands still during the session's 3 s get-ready
countdown (mic open, nothing streamed yet): the median level of 50 ms blocks of the same 16 kHz
audio, with the same dBFS formula as the service. The service then treats audio up to 10 dB above
it (clamped to -70...-30 dBFS) as **not speaking**: no pitch is measured, the typical volume is
learned only from speech, "Speak up" fires only for speaking quietly, and not speaking for 5 s
(once you've started) is a long pause ("Keep talking"; 150 ms of continuous voiced audio ends a
pause, so clicks and noise blips don't). Without
it, fixed levels are used (pitch ignored below -60 dBFS, anything under -45 dBFS is "too quiet").

```json
{"type": "script", "text": "Good morning everyone..." | null}   // the user's script; null/empty = off
```

Sent when the connection opens, when the script changes (once typing pauses), and before
`session_start` if it changed. The service keeps it across sessions until it changes.

```json
{"type": "target_time", "seconds": 600 | null}   // the time the user has to present; null = not set
```

Sent when the connection opens and whenever the Script card's "Time you have" field changes; kept
across sessions. It sets the **desired pace**: with a script, script words × 60 / `seconds`
(clamped to 40–250 WPM); without a script (or without a time), the Settings **Target pace**
slider (default 140). The live pace alert, the average pace alert and the final pace score all use
it, and it's also passed to the scorer as the expected length. Both `script` and `target_time` get
a `pace_target` reply.

The Voice calibration menu sends six seconds of raw 16 kHz mono float32 audio to
`/voice-calibration` in a single binary message. The service returns `{"baseline": {...}}` using
the median voiced pitch and volume from that sample, or `{"error": "..."}` if it cannot measure
enough speech. The app saves the values locally and sends them as `voice_baseline` on each
`session_start`. They stay fixed throughout practice. A null baseline leaves pitch and volume
checks unmeasured. The room's silence level (`noise_floor`) is still measured per session.

The app sends both controls for camera-only practice too. Raw audio is optional.

## Python → Electron

### `session_score`: after the final transcript

Contains `score` (a percentage, or null without measured data), `measured_points` (how much of
the 100 was measured), `categories` (filler, voice tone, speaking pace, body language, script
accuracy and time limit breakdowns, each with its fixed `weight`), `feedback` (tips ordered by deduction), and `checkpoints` (mistakes
with a `category`, `label`, and `at` in seconds from recording start). Voice incidents come from the existing voice detector.
Webcam incidents use the configured hold durations and count each sustained condition once.

**Fixed weights (out of 100):** filler words 15, voice tone 15 (volume 5, pitch 5, quiet 5),
speaking pace 10 (pace near the target 5, steady pace 5), body language 25 (looking down 4, face
framing 6, shoulder tilt 2.5, repeated gestures 3.5, no hand movement 5, fast hands 4), script
accuracy 25 (coverage 15, order 5: 1 per skip ahead / step back, off script 5: 1 per stretch
matching no part) and time limit 10 (none lost up to 5% over the limit, all at 30% over).
Criteria that weren't measured (no script, no time limit, camera off, alert turned off) are left
out; `score` is the percentage of the measured points kept. Every number is rounded to 1 decimal
(halves up). The point values below predate the fixed weights; each check keeps its share.

When **Hands not visible** is enabled, separate losses of hand visibility lasting one second
by default cost 0.75 points per incident per minute, up to the remaining points for enabled hand
checks. Sessions under one minute use one minute for this calculation.
The first two separate framing failures each deduct up to 2.5 measured body-language points.
Each failure must last for the configured framing duration, which defaults to one second.
Other body-language checks lose 75% on the first sustained failure
and all credit on the second, regardless of session length.
Voice tone is worth up to 6 rubric points. Pitch swings cost both pitch points on the first
qualifying violation. The default is 6 semitones from the saved reference for 0.3 seconds,
allowing short unvoiced gaps between readings. Abrupt octave-like jumps need repeated readings
before they appear on the graph or count toward an incident.
Volume spikes and low volume are worth 2 points each, with 75% lost on the first sustained
failure and all credit lost on the second. Banner cooldowns do not affect incident counts.
Quiet audio above the speech cutoff can count without a pitch reading. The cutoff uses the
room measurement, bounded by the saved voice calibration to leave room for quiet speech.
Below the cutoff, a detectable pitch is required. Short pauses preserve the incident without
adding to its duration.
The individual Live alerts toggles at session end determine which checks count. A disabled check
contributes neither deductions nor available points. **Average pace** controls the average pace
check, and **Live pace** controls the consistency check. Script accuracy is a combined check and
is excluded if any of the script alerts are disabled. The Silent display style only hides banners.

Speaking pace is measured from final transcript word timings. Its four points are split equally
between a moderate average pace and consistency across roughly 20-word samples. The default target
is the desired pace (see `target_time`; 140 WPM by default, full pace credit within 40 WPM of it). Sessions with fewer than 20 recognized words or less
than eight seconds of active speech leave pace unmeasured.

### `voice`: every 100 ms

```json
{"type": "voice", "pitch_hz": 182.4, "pitches": [179.8, 182.4], "volume_dbfs": -31.2,
 "pitch_times": [12.45, 12.5], "captured_at": 12.5, "wpm": 140,
 "typical_hz": 168.0, "typical_volume_dbfs": -25.0, "quiet_limit_dbfs": -33.0, "spike_hz": 237.6,
 "speaking": true, "silence_dbfs": -49.9}
```

`captured_at` gives the latest reading's time on the renderer's monotonic clock, or `null`
when audio arrives without timestamps. `silence_dbfs` is the effective speech cutoff (null if the room
wasn't measured). Audio below this cutoff can still count as speech if it has a detectable pitch.
`quiet_limit_dbfs` is the calibrated low-volume threshold shown on the volume graph.
`pitch_hz` is the latest reading, `null` when the audio isn't voiced (silence, breath, s/t/k
sounds). `pitches` holds every 50 ms reading since the last message, oldest first; the Voice pitch
graph plots these. `pitch_times` gives their corresponding capture times, or `null` entries for
untimed audio. `wpm` is the provisional speaking pace. `typical_hz` and `typical_volume_dbfs`
are the saved calibration values, or `null` before calibration. `spike_hz` is the upper pitch limit. The lower limit is the same number of
semitones below `typical_hz`. Crossing either limit for the configured duration triggers the alert.

### `transcript`: whenever the transcript changes

```json
{
  "type": "transcript",
  "new_sentences": ["[So] I think [um] the results are really good."],
  "partial": "the main idea is",
  "new_fillers": ["um"],
  "fillers": {"um": 1, "uh": 0, "like": 0, "you know": 0, "so": 1, "filler?": 0},
  "total": 2,
  "wpm": 138.4,
  "avg_wpm": 131.0,
  "desired_wpm": 145.0,
  "desired_source": "script"
}
```

| Field | Meaning |
|---|---|
| `new_sentences` | Finished sentences to **append**, counted fillers in `[brackets]`. A sentence ends when Moonshine hears a pause |
| `partial` | Words of the sentence still being spoken; **replaces** the previous partial |
| `new_fillers` | Fillers detected since the last message (Electron flashes the counter) |
| `fillers`, `total` | Running counts for the session, including fillers already heard in the current partial |
| `wpm` | Live words per minute (last few seconds, including the line still being spoken); `null` while the speaker pauses |
| `avg_wpm` | Average of the last 3 finished lines (each ends at a pause): their words × 60 / the time from the first one's start to the last one's end, so the pauses between them count. `null` until there are 3 lines |
| `desired_wpm`, `desired_source` | The pace to aim for; `script` = script words / the time the user has, `setting` = the Target pace slider |

### `pace_target`: reply to `script` and `target_time`

```json
{"type": "pace_target", "desired_wpm": 130.0, "source": "script", "script_words": 1300, "target_seconds": 600}
```

The Script card shows it before the session ("Desired pace: 130 WPM (1,300 words in 10 min)").

Filler markers inside sentences: `[um]`, `[like]`, `[you know]`... = counted as a filler. A "like" / "so" /
"you know" without brackets was used as an ordinary word ("I'd like to") and isn't counted.

### `script_loaded`: reply to `script` (and again once the comparison model has loaded)

```json
{"type": "script_loaded", "chunks": ["Good morning everyone...", "..."], "ready": true}
```

`chunks` is the script split into parts (sentences, very short ones merged); `[]` = no script.
`ready` is false while the comparison model is still loading.

### `script_position`: at most every 0.5 s while new words arrive (only with a script)

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

The Settings tab's `volume`, `pitch`, `quiet`, `pause` and `filler` rules (sent in the webcam
side's `settings` message) control these: on/off, threshold (dB above / semitones in either
direction / dB below normal), minimum duration (for `pause`, how long a pause may be), and
cooldown. Fillers are always counted in the transcript.

The audio side posts alerts for 2.5 s. The webcam side adds them to the overlay, and the
`voice` message's `alerts` list carries them for audio-only practice:
`Speak up` (speaking too quietly), `Keep talking` (long pause), `Steady your volume`, `Steady your pitch`, and
`Filler: um` (etc.) each time a filler is detected. The current voice reading also appears as a
status line (`voice: 182 Hz  -31 dBFS`).

Two pace alerts, both against the desired pace (see `target_time`):

- **Live pace** (rule `pace`): the live WPM of the last 5 s more than **30%** below / above the
  desired pace → `Pick up the pace` / `Slow down`. Its threshold slider is the **Target pace**
  (the desired pace when there's no script + time).
- **Average pace** (rule `pace_avg`): checked when a line finishes; `avg_wpm` more than the rule's
  threshold (default **15%**, slider 5–40%) below / above the desired pace →
  `Falling behind: speed up` / `Too fast: slow down`.

Their cooldowns stop the same cue from repeatedly interrupting the presentation.

## How the transcript and fillers work

1. **Moonshine** (medium streaming, on-device) produces the transcript: partial text while you talk, a
   finished line at each pause. It's biased towards writing um / uh, which it otherwise tends to leave out.
2. An um / uh that at least two partials showed but the finished line dropped is put back.
3. Fillers are found in the text (`fillers.py`): um / uh / erm / hmm always count; "like", "so" and
   "you know" only when the neighbouring words say they're fillers.
4. Alerts and live counts come from the partials, the final totals from finished lines only.

Measured accuracy, delay and cost: `service/benchmarks/README.md`.
