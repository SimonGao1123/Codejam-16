"""
The whole service at once, MINUTES into a session: fills the session with benchmark audio, then streams
audio and ~30 webcam frames/s in real time for a minute, with a script to track. Measures the webcam
round trip, the data on the webcam socket and the service's CPU.

  uv run --group bench python -m benchmarks.webcam_stall [--minutes 10] [--service path/to/other/service --full]
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
import websockets

from .frames import compact, frames, wire

BENCH = Path(__file__).parent
PORT = 9377
SECONDS = 60


def audio_and_script():
    parts, script = [], []
    for path in sorted((BENCH / "data/dev").glob("ds_*.wav")):
        with wave.open(str(path)) as w:
            parts.append(np.frombuffer(w.readframes(w.getnframes()), "<i2"))
        said = {}
        for w in json.loads(path.with_suffix(".json").read_text())["words"]:
            if w.get("tag") != "F":
                said.setdefault(w["s"], []).append(w["w"])
        script += [" ".join(ws).capitalize() + "." for ws in said.values()]
    return np.concatenate(parts).astype("<f4") / 32768, " ".join(script)


def cpu_seconds(pid):
    minutes, seconds = subprocess.check_output(["ps", "-o", "cputime=", "-p", str(pid)], text=True).strip().split(":")[-2:]
    return int(minutes) * 60 + float(seconds)


async def connect(path):
    for _ in range(300):
        try:
            return await websockets.connect(f"ws://127.0.0.1:{PORT}/{path}", max_size=None)
        except OSError:
            await asyncio.sleep(0.2)
    raise RuntimeError("the service didn't start")


async def measure(minutes, pid, full):
    (pcm, script), filled = audio_and_script(), int(minutes * 60 * 16000)
    messages = [wire(f if full else compact(f)) for f in frames(SECONDS + 5)]
    ws = await connect("audio")
    ready, done, seen = asyncio.Event(), asyncio.Queue(), {"script_position": 0}

    async def read():
        async for raw in ws:
            msg = json.loads(raw)
            if msg["type"] == "audio_processed":
                done.put_nowait(msg["id"])
            elif msg["type"] == "script_loaded" and msg["ready"]:
                ready.set()
            elif msg["type"] in seen:
                seen[msg["type"]] += 1

    reader = asyncio.create_task(read())
    await asyncio.sleep(8)                              # the script comparison model loads in the background
    await ws.send(json.dumps({"type": "script", "text": script}))
    await asyncio.wait_for(ready.wait(), 30)
    await ws.send(json.dumps({"type": "session_start"}))
    start = time.perf_counter()
    for i in range(0, filled, 16000):
        await ws.send(pcm[i:min(i + 16000, filled)].tobytes())
        await ws.send(json.dumps({"type": "audio_sync", "id": i}))
        await done.get()
    fill = time.perf_counter() - start

    cam = await connect("webcam")
    await cam.send(json.dumps({"type": "settings", "rules": {}, "display": []}))       # the app's default: no stats
    trips, received, sent = [], 0, 0
    cpu, positions, start = cpu_seconds(pid), seen["script_position"], time.perf_counter()

    async def speak():
        for n in range(SECONDS * 10):
            await ws.send(pcm[filled + n * 1600:filled + (n + 1) * 1600].tobytes())
            await asyncio.sleep(max(0, start + (n + 1) * 0.1 - time.perf_counter()))

    async def film():                              # one frame in flight, late ones dropped, like the app
        nonlocal received, sent
        tick = 0
        while time.perf_counter() - start < SECONDS:
            begin = time.perf_counter()
            await cam.send(messages[tick])
            reply = await cam.recv()
            trips.append((time.perf_counter() - begin) * 1000)
            sent += len(messages[tick])
            received += len(reply)
            tick = int((time.perf_counter() - start) * 30) + 1
            await asyncio.sleep(max(0, start + tick / 30 - time.perf_counter()))

    await asyncio.gather(speak(), film())
    cpu = cpu_seconds(pid) - cpu
    reader.cancel()
    q = lambda p: np.percentile(trips, p)                    # noqa: E731
    print(f"{minutes:g} min in: catching up took {fill:.0f} s; {len(trips) / SECONDS:.1f} frames/s; round trip ms "
          f"p50 {q(50):.2f} p95 {q(95):.2f} p99 {q(99):.2f} max {max(trips):.0f}; webcam KB/s sent "
          f"{sent / SECONDS / 1000:.1f} received {received / SECONDS / 1000:.2f} ({sent / len(trips):.0f} + "
          f"{received / len(trips):.0f} bytes per frame); service CPU {cpu / SECONDS:.2f} cores; "
          f"{seen['script_position'] - positions} script updates", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--service", default=str(BENCH.parent), help="another checkout's service folder to measure")
    ap.add_argument("--full", action="store_true", help="send every landmark (services from before the compact frames)")
    args = ap.parse_args()
    proc = subprocess.Popen([sys.executable, "-m", "presentation_service", "--port", str(PORT)], cwd=args.service,
                            env={**os.environ, "PYTHONPATH": args.service}, stderr=subprocess.DEVNULL)
    try:
        asyncio.run(measure(args.minutes, proc.pid, args.full))
    finally:
        proc.kill()
