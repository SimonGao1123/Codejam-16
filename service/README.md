# Presentation Helper analysis service

Local Python service the Electron app connects to. Electron runs MediaPipe and captures the mic;
this service does the calculations and sends back what to show.

| WebSocket | In | Out | Code |
|---|---|---|---|
| `ws://127.0.0.1:8766/webcam` | pose / hand / face landmarks per frame | overlay state: posture, eye level, hand motion, alerts | `webcam_analysis.py` |
| `ws://127.0.0.1:8766/audio` | 16 kHz mic audio | voice pitch / volume, live transcript, filler counts | `audio_analysis.py`, `voice.py`, `transcript.py` |

Message formats: `docs/webcam-protocol.md`, `docs/audio-protocol.md`. The analysis is ported from
`experiments/` (`mediapipe_webcam/webcam_test.py`, `gap_heuristic/voice_pitch.py`,
`gap_heuristic/SpeechtoText.py`).

## Run

You normally don't: the Electron app (`cd app && npm run dev`) starts this service on a free port,
passes the port to the page, and stops the service when the app quits (`app/src/main/analysisService.ts`).
The service also exits by itself if the app crashes (`--exit-with-parent`: it watches stdin).

To run it yourself, e.g. to see the filler checks:

```sh
cd service
uv run python -m presentation_service --debug            # listens on 8766
ANALYSIS_SERVICE_PORT=8766 npm run dev                   # in app/: connect to it instead of starting one
```

Options: `--port 8766`, `--whisper base.en` (more accurate um/uh check, slower), `--whisper ''`
(no Whisper: suspected fillers show as `[um?]`), `--debug` (log every filler check).

- Vosk model: `experiments/gap_heuristic/vosk-model-small-en-us-0.15` (in the repo).
- Whisper `tiny.en` downloads to `~/.cache/huggingface/` on first run (~75 MB, needs internet once).

## Test

```sh
uv run pytest -q      # the speech tests use macOS `say`; they're skipped elsewhere
```
