# Codejam-16
Presentation Helper App: practice a presentation while it watches your posture, eye level and
hands, listens to your pitch, volume and filler words, and shows a live transcript. Runs locally.

| Folder | What |
|---|---|
| `app/` | Electron + React app (camera, MediaPipe, microphone, all the UI) |
| `service/` | Python analysis service the app connects to (`service/README.md`) |
| `docs/` | Message formats between the two |
| `experiments/` | Standalone prototypes the service was built from |
| `models/` | MediaPipe models |

## Run

```sh
cd app && npm install && npm run dev
```

The app starts the Python service in the background (needs [uv](https://docs.astral.sh/uv/)) and
stops it when you quit. The service's output appears in the same terminal, prefixed `[service]`.
