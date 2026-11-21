# Webcam analysis protocol (Electron ↔ Python)

The Electron app captures the webcam, runs MediaPipe (pose, hands, face) on the GPU and draws the
skeleton itself. It sends the raw landmarks to the **webcam analysis service** (Python), which does
all the calculations and replies with what to draw on top of the video.

- Transport: one WebSocket at **`ws://127.0.0.1:8766`**, JSON text messages.
- TypeScript types: `app/src/renderer/src/webcam/protocol.ts`. Keep this file and that one in sync.
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

### `calibrate`: the user clicked Calibrate (or pressed `c`)

```json
{"type": "calibrate", "t": 12.345}
```

The service runs the 2 s get-into-position pause and the 2 s recording, timed by the `t` of the
frames that follow.

## Python → Electron

### `state`: send one per `frame` (the overlay Electron draws until the next one arrives)

```json
{
  "type": "state",
  "t": 12.345,
  "lines": [{"text": "shoulder tilt: +1.2 deg  (+0.4 vs calibrated)", "color": "white"}],
  "alerts": ["Look up", "Use your hands"],
  "rest_circles": [{"x": 0.35, "y": 0.8, "r": 0.06, "active": true}],
  "hand_labels": [{"x": 0.35, "y": 0.75, "text": "L +12 deg"}],
  "calibration": {"phase": "idle", "remaining": 0, "calibrated": true, "message": "Calibrated: ..."}
}
```

| Field | Drawn as |
|---|---|
| `lines` | Status lines, top-left, under Electron's own FPS line. `color`: `white` / `red` / `yellow` / `green` |
| `alerts` | Red banners, centered, stacked below the status lines |
| `rest_circles` | Thin circles; `r` is a fraction of the video width; `active` = green |
| `hand_labels` | Yellow text next to the point (Electron offsets it by +10, +20 px) |
| `calibration.phase` | `idle` / `waiting` (big countdown from `remaining`) / `recording`; also sets the button label |
| `calibration.message` | Line under the status lines; green once `calibrated`, else yellow |

## What the service computes

Everything that `experiments/mediapipe_webcam/webcam_test.py` does after MediaPipe, on the `webcam`
branch: `Calibration`, `HandMotion`, `Alert`, `shoulder_tilt`, `head_pitch`, `eyes_down`,
`hand_angle`, `match_hands`, and the thresholds at the top of that file. Those functions read
landmarks with `.x` / `.y` / `.z` / `.visibility`, so wrapping each `[x, y, z, v]` list in a small
object is enough to reuse them.

## Recordings

The Record button saves `~/Movies/Presentation Helper/<time>.webm` (webcam video + mic track) and
`<time>.jsonl`. The first line of the `.jsonl` is `{"type": "recording", "t": ..., "video": ...}`,
giving the frame clock time when the video started. Every following line is a message exactly as
sent to the service, so feeding the lines to the service in order replays the session.
