# Webcam analysis protocol (Electron ↔ Python)

The Electron app captures the webcam, runs MediaPipe (pose, hands, face) on the GPU and draws the
skeleton itself. It sends the raw landmarks to the **webcam analysis service** (Python), which does
all the calculations and replies with what to draw on top of the video.

- Transport: WebSocket at **`ws://127.0.0.1:<port>/webcam`**, JSON text messages. The port is chosen by the Electron app,
  which starts `service/` itself (`app/src/main/analysisService.ts`); the same service also serves the audio side at `/audio`
  (`audio-protocol.md`).
- TypeScript types: `app/src/renderer/src/practice/camera/webcamProtocol.ts`. Keep this file and that one in sync.
- Electron reconnects every second if the service isn't running, and drops frames instead of
  queueing them when the service falls behind.

## Coordinates

- `x`, `y` are normalized 0–1 in the **mirrored** (selfie) view, the same as `webcam_test.py`, which
  flipped the frame before running MediaPipe. Multiply by `width` / `height` for pixels.
- `z` is MediaPipe's depth, roughly in the same units as `x` (smaller = closer to the camera).
- `handedness` is also as seen in the mirrored view.

## Electron → Python

### `frame`: sent for every processed camera frame (~30 per second)

```json
{
  "type": "frame",
  "t": 12.345,
  "width": 1280, "height": 720,
  "pose": [[x, y, z, visibility], ...33] | null,
  "hands": [{"handedness": "Left", "score": 0.97, "landmarks": [[x, y, z], ...21]}],
  "face": {"landmarks": [[x, y, z], ...478], "blendshapes": {"eyeLookDownLeft": 0.12, ...}} | null
}
```

`t` is seconds on a monotonic clock. Use it for all timing (alert hold times, calibration
countdown, speeds) instead of the wall clock, so recorded sessions replay identically.

### `calibrate`: sent automatically at the start of every practice session

```json
{ "type": "calibrate", "t": 12.345, "delay": 0, "duration": 3, "when_ready": true }
```

There's no manual calibration. At the start of each session (including "Start another session")
the app sends this as soon as frames are analysed and the service is connected. With
`when_ready`, the service **waits** (phase `arming`) until the face, both shoulders and both hands
are in frame and the speaker holds fairly still (wrists and shoulders barely moving) for 0.75 s,
then records 3 s of the resting position: shoulder tilt, head pitch, eye gaze, resting hands.
While waiting, `calibration.message` says what's missing ("Waiting to calibrate: show both hands,
hold still"); the overlay shows it large and the status chip under the video repeats it. If the
speaker leaves the frame or moves for 0.4 s during the recording, it starts over ("Calibration
restarted: ..."), so a bad moment can't become the baseline. Posture / eye-level alerts and
scoring are paused until calibration is done. Without `when_ready`, recording starts after
`delay` seconds regardless (defaults 2 s pause, 2 s recording).

### `settings`: the Live alerts tab's rules

Sent when the connection opens and whenever a rule changes on the Live alerts tab.

```json
{"type": "settings", "rules": {
  "tilt": {"enabled": true, "threshold": 6, "duration": 1.5, "cooldown": 0},
  "face": {"enabled": false, "threshold": null, "duration": 1, "cooldown": 0},
  ...
}}
```

| Rule id | Alert | Threshold |
|---|---|---|
| `tilt` | Level your shoulders | degrees from calibrated |
| `look_down` | Look up | degrees of head pitch below calibrated |
| `face` | Face the camera | — |
| `hands_missing` | Show both hands or step back | — |
| `still` | Use your hands | — |
| `fast` | Slow your hands | wrist speed, shoulder widths / s |
| `repeat` | Vary your gestures | % self-similarity (0.8 score = 80) |
| `volume`, `pitch`, `quiet`, `pause`, `filler` | voice / filler alerts (applied by the audio side, see `audio-protocol.md`) | |
| `off_script`, `out_of_order`, `stuck` | script alerts, only with a script (audio side, see `audio-protocol.md`) | % match needed / — / × expected time |

`duration` = seconds the problem must last before the alert shows; `cooldown` = seconds after an
alert disappears before it can show again; a disabled rule never shows. Without a `settings`
message the service uses the same defaults as the Live alerts tab. The alert *style* (banner /
compact / silent) isn't sent: Electron applies it when drawing `alerts`.

## Python → Electron

### `state`: send one per `frame` (the overlay Electron draws until the next one arrives)

```json
{
  "type": "state",
  "t": 12.345,
  "lines": [
    {
      "text": "shoulder tilt: +1.2 deg  (+0.4 vs calibrated)",
      "color": "white"
    }
  ],
  "alerts": ["Look up", "Use your hands"],
  "rest_circles": [{ "x": 0.35, "y": 0.8, "r": 0.06, "active": true }],
  "hand_labels": [{ "x": 0.35, "y": 0.75, "text": "L +12 deg" }],
  "calibration": {
    "phase": "idle",
    "remaining": 0,
    "calibrated": true,
    "message": "Calibrated: ..."
  }
}
```

| Field                 | Drawn as                                                                                             |
| --------------------- | ---------------------------------------------------------------------------------------------------- |
| `lines`               | Status lines, top-left, under Electron's own FPS line. `color`: `white` / `red` / `yellow` / `green` |
| `alerts`              | Drawn in the Live alerts style: banner (wide yellow box), compact (pill at the top right), silent (none) |
| `rest_circles`        | Thin circles; `r` is a fraction of the video width; `active` = green                                 |
| `hand_labels`         | Yellow text next to the point (Electron offsets it by +10, +20 px)                                   |
| `calibration.phase`   | `idle` / `arming` (waiting for the speaker: `message` drawn large) / `waiting` / `recording` (big countdown from `remaining`); also the status chip under the video |
| `calibration.message` | Line under the status lines; green once `calibrated`, else yellow                                    |

## What the service computes

`service/presentation_service/webcam_analysis.py`: the analysis from
`experiments/mediapipe_webcam/webcam_test.py`, ported without OpenCV/MediaPipe (`Calibration`,
`HandMotion`, `Alert`, `shoulder_tilt`, `head_pitch`, `eyes_down`, `hand_angle`, `match_hands`, and
the same thresholds). Each `[x, y, z, v]` list is wrapped in a small object with `.x` / `.y` / `.z`
/ `.visibility`, so the functions are unchanged.

The audio side adds to each `state`: a `voice: ... Hz ... dBFS` status line (with "(not speaking)"
at the room's silence level), and voice / filler / script alerts in `alerts` (see
`audio-protocol.md`).

## Recordings

Practice records automatically after the countdown. Stop presenting finishes the WebM and makes it
available for playback. In development, the WebM and matching JSONL log are saved to
`app/temp/recordings/` with a shared ID. Packaged builds use the app's user data directory.
The first line of the `.jsonl` is `{"type": "recording", "t": ..., "video": ...}`,
giving the frame clock time when the video started. Every following line is a message exactly as
sent to the service, so feeding the lines to the service in order replays the session.
