# Presentation Helper — App Context

A downloadable desktop app that watches and listens while you practice a presentation, gives **live alerts** when something goes wrong, and gives a **full evaluation at the end**. Everything runs **locally** (no cloud): speech-to-text and video analysis happen on the user's machine.

Target users: students and anyone preparing for presentations / public speaking.

---

## Features (priority order)

| # | Feature | What it checks |
|---|---|---|
| 1 | Posture | Slouching, leaning, head pushed forward (vs a calibrated baseline) |
| 2 | Hand movement | Repetitive gestures (same motion looping), no hand use for a long time, hands out of frame |
| 3 | Live alerts | Customizable rules: thresholds, on/off per rule, alert style, sensitivity |
| 4 | Filler words | um / uh (sounds) + like / you know / basically / so (words) |
| 5 | Script comparison | Going off script / off topic, skipped sections |
| 6 | Voice pitch / volume | Spikes, too quiet for too long, long silences, monotone |
| 7 | Eye contact | Looking at camera vs down at notes / away |

Additions worth including:
- **Speaking pace (words/min)**: nearly free once there's a transcript
- **End-of-session report**: per-category scores, a timeline with clickable alert markers, progress across sessions, a short coaching summary

---

## Architecture

```
┌──────────── Electron (React + TypeScript) ─────────────────┐
│  UI, webcam preview, alert overlay                         │
│  MediaPipe (JS, GPU) ──► posture / hands / gaze signals    │
│  Web Audio ──► pitch, volume, silence                      │
└──────────────┬─────────────────────────────────────────────┘
               │ local WebSocket (audio chunks out, events back)
┌──────────────▼─── Python sidecar ──────────────────────────┐
│  Silero VAD → faster-whisper (streaming) → transcript      │
│  filler detection · pace · script matching                 │
│  rules engine → alerts · session log (SQLite)              │
│  end of session: scoring + local LLM coaching summary      │
└────────────────────────────────────────────────────────────┘
```

### Decisions made
- **Desktop app, not a web app**, so local models can run fully offline.
- **Electron over React Native.** React Native is mobile-first and its desktop support is weak. The app relies on browser APIs (getUserMedia, Web Audio, MediaPipe JS, canvas), which Electron gets for free from Chromium. Tauri is a lighter alternative, but camera and audio behave differently across the OS webviews it uses.
- **Vision runs in Electron** (MediaPipe JS on the GPU) so skeleton overlays draw without lag. Video frames never go to Python.
- **Language/audio ML runs in a Python sidecar**, because those libraries are Python-first. Package it with PyInstaller.
- Every feature writes **timestamped events to one session log**. Live alerts and the final report both read from it.

---

## Models

| Job | Model | Notes |
|---|---|---|
| Voice activity detection | **Silero VAD** | ~2 MB, ships inside the `silero-vad` pip package |
| Live transcript | **faster-whisper `small.en`** (`base.en` if slow) | ~485 MB, downloaded to `~/.cache/huggingface/hub/` |
| Accurate filler count (after session) | **CrisperWhisper** | Whisper large-v3 fine-tuned for verbatim transcription. Too slow for live use. **License: CC BY-NC (non-commercial)** |
| Pose / hands / face+iris | **MediaPipe** Pose, Hand, Face Landmarker | `.task` files of a few MB each |
| Script matching | **all-MiniLM-L6-v2** (sentence-transformers) | ~80 MB embeddings |
| Pitch / volume | No model: Web Audio + `pitchy` | |
| Coaching summary | **Llama 3.2 3B or Qwen 2.5 7B** via Ollama | Only writes text. Scores come from metrics, so the LLM can't make them up |

MediaPipe model URLs:
- `https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task`
- `https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task`
- `https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task`

Avoid **YOLO-pose** (Ultralytics). Its AGPL license would force the app to be open-sourced if distributed.

---

## Filler word detection (the hardest part)

**Problem:** most speech-to-text models are trained on cleaned-up transcripts and **drop um/uh**. The earlier Vosk prototype (NoFiller repo) couldn't detect them. Whisper also drops them by default.

**Approach, in layers:**
1. **Whisper with a filler prompt:** `initial_prompt="Um, uh, so, like, I mean, um, you know, uh, basically."` nudges Whisper to write fillers. Tested: without the prompt, Whisper dropped every filler in one test clip; with it, it caught most. It helps a lot but doesn't guarantee anything.
2. **Gap heuristic (backup):** if VAD heard speech but Whisper wrote no word there, and the gap is 0.2–1.5 s long and loud enough (≥35% of the utterance's average volume), flag a likely filler `[um?]`. It **doesn't recognize "um"**; it only finds *unexplained sound*, so coughs, laughs and "hmm" can trigger it. Gaps within 0.3 s of a filler Whisper already wrote are dropped to avoid double counting.
3. **Filler classifier (optional upgrade):** a small audio classifier trained on **PodcastFillers** (Adobe), run only on flagged gaps to confirm or reject them.
4. **CrisperWhisper after the session** on the full recording, for an accurate count in the final report.

Words like "like" and "so" depend on context. Classify them after the session (e.g. with the LLM), not live.

### PodcastFillers dataset
- Download: https://zenodo.org/records/7121457 (v1.0.1) · Site: https://podcastfillers.github.io
- Paper: Zhu, Caceres & Salamon, *Filler Word Detection and Classification: A Dataset and Benchmark*, Interspeech 2022 (arXiv:2203.15135)
- 199 English podcast episodes, ~35K labeled fillers (uh/um), ~50K labels for other sounds (useful negatives like breath and laughter), pre-cut 1-second clips
- Rejoin the archive: `zip -FF PodcastFillers.zip --out PodcastFillers-full.zip`
- **License: non-commercial research only.** Fine for a codejam or school project, not for a paid product.
- Caveat: it's podcast-quality audio. Users will be on laptop mics, so add noise and room echo to the training clips or include your own recordings.

---

## Body language logic

- **Posture:** calibrate a baseline in the first ~5 s; alert on drift. Signals: shoulder tilt (landmarks 11/12), neck ratio = (shoulder height − nose height) / shoulder width, which drops when slouching or pushing the head forward.
- **No hand use:** wrist speed (normalized by shoulder width) below a threshold for ~10–20 s.
- **Repetitive gestures:** autocorrelation on ~10 s of wrist positions; a strong periodic peak means the same gesture is looping.
- **Eye contact:** Face Landmarker iris + head pose → "looking at camera" vs "looking down/away". True gaze tracking from a webcam is unreliable, so don't attempt it.

## Script comparison logic
Split the script into sentences and embed them once. Every few seconds, embed the last ~15 s of transcript and find the closest script chunk. Low similarity for N seconds → off-script alert. This also gives the current position in the script (teleprompter-style) and a "skipped sections" report.

## Live alert design
- A rule needs **duration** (the problem lasts ~2–3 s) and a **cooldown** (~30 s per rule) before firing. Cap how many alerts can show at once.
- Show alerts on the screen edge or as a one-word icon ("Slow down", "Off script"). People can't read sentences mid-talk.
- A silent mode only logs events for the final report.

---

## Experiments (`~/codejam_experiments/`)

Two separate uv projects on Python 3.12 (MediaPipe and the Whisper backend may not have wheels for Python 3.14 yet).

### `gap_heuristic/live_transcript.py`
Live mic transcript in the console with filler detection.
- `uv run live_transcript.py` (mic) · `--file clip.wav` · `--model base.en` · `--device auto|mps|cuda|cpu`
- The live line is re-transcribed every ~0.5 s while you talk (dim text). On a 0.6 s pause it's replaced by the final line with `[um]` (Whisper wrote it) / `[um?]` (gap heuristic). Ctrl+C prints a summary: fillers, fillers/min, words/min.
- The live-line refresh slows down automatically if the machine can't keep up.
- Performance (Apple M5, small.en, 10 s phrase): **MPS GPU 2.1 s final pass, real time; CPU 3.6 s.**
- Lessons learned:
  - faster-whisper on `device="mps", compute_type="int8_float16"` is ~3× faster than CPU (experimental backend)
  - `torch.set_num_threads(1)` is required, otherwise Silero and Whisper fight over CPU threads (finalizing a 10 s phrase took 17 s → 3.6 s on CPU)
  - `av` is pinned `<17`; newer PyAV breaks faster-whisper's `decode_audio`
  - use two separate Silero model instances (streaming iterator vs `get_speech_timestamps`, which resets model state)

### `mediapipe_webcam/webcam_test.py`
Webcam with pose + hand skeleton and live metrics: shoulder tilt, neck ratio (press `c` to calibrate), hand motion / seconds still. Uses the MediaPipe Tasks API (mediapipe 1.2.3); models download to `models/` on first run.

### Status
- Both run end to end. Tested only with macOS `say` audio and a blank video frame, **not yet on real voice or webcam**.
- In tests, all fillers came from Whisper transcribing them; the gap heuristic hasn't fired on a real miss yet. TTS "um"s are clearer than real nervous "uh"s, so test with real speech.

---

## Open risks / next steps
1. **Real-voice filler test:** record 2–5 min with lots of um/uh; compare plain Whisper vs prompted Whisper vs CrisperWhisper vs gap heuristic against what was actually said.
2. **Laptop load:** STT + MediaPipe + UI at the same time. Benchmark on the weakest team laptop (and Windows, which has no MPS).
3. **Packaging:** get an Electron app with a bundled Python sidecar installing on Mac + Windows before building features on it.
4. **Licensing:** CrisperWhisper and PodcastFillers are non-commercial only. Fine for a codejam; revisit if the app is ever sold.
5. Port the webcam test to MediaPipe JS inside Electron, and replace the console output with WebSocket events (`{"type": "filler", "t": 12.4}`) sent to the rules engine.
