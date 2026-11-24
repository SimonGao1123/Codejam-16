# Presentation Helper (Codejam-16)

A desktop app for rehearsing presentations. While you practise, it watches your posture, eye
level and hands through the webcam, listens to your voice, transcribes what you say, counts filler
words, follows along with your script, and pops up live alerts when something drifts. At the end
you get a score with feedback and a playback of your recording. Everything runs locally on your
computer: no cloud, no account.

**Features**
- **Posture & body language:** shoulder tilt, looking down, face out of frame, hands not visible,
  no / too fast / repeated hand movement (MediaPipe pose, hands and face on the GPU)
- **Voice:** live pitch graph, pitch and volume spikes, speaking too quietly
- **Transcript & filler words:** live transcript with a counter for um, uh, like, you know, so
  (Vosk speech-to-text + Whisper double-checks hesitations)
- **Script tracking (optional):** paste your script or attach a PDF; the app shows which part
  you're on and warns when you skip ahead, go back, go off script, or get stuck
- **Live alerts:** turn each alert on/off and tune its threshold, timing and style (banner,
  compact, silent) on the Settings tab
- **Results:** a delivery score with feedback, plus a recording to play back with alert markers

## Setup

You need **[Node.js](https://nodejs.org/) 20+** and **[uv](https://docs.astral.sh/uv/)**
(Python package manager; it installs the right Python by itself).

```sh
git clone https://github.com/SimonGao1123/Codejam-16.git
cd Codejam-16

cd service && uv sync          # Python service: speech, voice, posture analysis
cd ../app && npm install       # Electron app
```

`npm install` may warn about a blocked install script for `electron-winstaller`. It's only
needed for building the Windows installer; ignore it for development.

## Run

```sh
cd app
npm run dev
```

The app starts the Python service in the background and stops it when you close the window. The
service's log appears in the same terminal, prefixed `[service]`. On macOS, allow camera and
microphone access when asked.

**First run** downloads two small models (needs internet once):
Whisper `tiny.en` (~75 MB, to `~/.cache/huggingface/`) and the script comparison model
all-MiniLM-L6-v2 (~90 MB, to `~/.cache/fastembed/`). The app works while they download; filler
double-checking and script tracking switch on when they're ready.

### Optional: more accurate speech-to-text

The repo includes Vosk's small English model, which the service uses by default. For better
transcripts, download the large model (1.8 GB download, 2.7 GB unzipped) into `models/`. The
service uses it automatically when it's there (it's git-ignored):

```sh
cd models
curl -LO https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip
unzip vosk-model-en-us-0.22.zip && rm vosk-model-en-us-0.22.zip
```

It makes the service slower to start (several seconds to load).

## Using it

1. **Practice tab:** optionally paste your script or attach a PDF in the Script card (text PDFs
   only; scanned pages can't be read).
2. **Start practice.** Stay quiet during the 3 s "Get ready" countdown (it measures the room's
   silence). Then stand in your resting position with your face, shoulders and both hands in
   frame and hold still: calibration starts by itself once you're in frame and still, and takes
   3 s. The session (timer, recording, transcript, alerts, score) only starts once it's done.
   Posture and eye-level alerts are measured against it. Your typical pitch and volume are
   learned as you talk (the last 10 s of speech).
3. Present. Alerts pop up on the video; the pitch graph, transcript, filler counter and script
   progress update live.
4. **End practice** to see your score, feedback and the playback.
5. **Settings tab:** choose which alerts to show, how sensitive they are, and their style. The
   body skeleton and live stats over the webcam are off by default; turn them on here, and pick which parts
   of each to show under Advanced settings.

## Project layout

| Folder | What |
|---|---|
| `app/` | Electron + React + TypeScript app: camera, MediaPipe, microphone capture, all the UI |
| `service/` | Python analysis service the app talks to over local WebSockets (`service/README.md`) |
| `docs/` | Message formats between the app and the service (`webcam-protocol.md`, `audio-protocol.md`) |
| `experiments/` | Standalone prototypes the features were built from (each a separate uv project) |
| `models/` | MediaPipe models (+ the optional large Vosk model) |

**How it fits together:** the app runs MediaPipe itself and sends only landmark numbers (never
video) to the service at `ws://127.0.0.1:<port>/webcam`; it streams the microphone as raw 16 kHz
audio to `/audio`. The service sends back what to draw, the transcript, voice readings, script
position and alerts. The app picks a free port and starts/stops the service itself
(`app/src/main/analysisService.ts`).

## Development

```sh
# tests
cd service && uv run pytest -q                    # speech tests use macOS `say`
cd experiments/script_compare && uv run pytest -q

# app checks and build
cd app
npm run typecheck
npm run lint
npm run build:mac                                  # or build:win / build:linux
```

To run the service yourself (e.g. to see its debug logs), start it first and point the app at it:

```sh
cd service && uv run python -m presentation_service --debug       # listens on 8766
cd app && ANALYSIS_SERVICE_PORT=8766 npm run dev
```

Packaged builds (`build:mac` etc.) include the Python service as a standalone program, so bundle it
first (without it the app still builds, but has no analysis). It's built for the system you're on:

```sh
cd service
uv run --no-dev --with "pyinstaller>=6,<7" pyinstaller run_service.py --name presentation-service \
  --onedir --noconfirm --paths . --collect-all moonshine_voice --collect-all fastembed
```

Keep the large Vosk model out of `models/` when building: everything in there goes into the app.

To release, set `version` in `app/package.json` and push the matching tag (e.g. `v1.0.0`). The
Release workflow builds the macOS (Apple Silicon, macOS 15+), Windows and Linux installers and puts
them in a draft GitHub release to check and publish.
