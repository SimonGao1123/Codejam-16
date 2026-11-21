"""
Script tracking test app: paste a script, talk, and watch where you are in it.

  - Live transcript from Vosk (same model as experiments/gap_heuristic)
  - The script split into chunks, each with its match score against what you just said
  - Current position (green) and the best match right now (yellow); chunks within the match
    tolerance of the best are outlined as "tied"
  - Alerts when you jump ahead (skipping chunks), go back, or go off topic

Sliders:
  Off-script threshold  the best chunk score has to reach this to count as "on script"
  Match tolerance       chunks within this much of the best score count as tied; among tied
                        chunks the one in the expected order wins (scripts often repeat ideas)

Run:   uv run script_compare_gui.py
       uv run script_compare_gui.py --wav talk.wav      (play a recording instead of the mic)
"""

import argparse
import json
import queue
import threading
import time
import tkinter as tk
import wave
from pathlib import Path
from tkinter import ttk

import numpy as np

from script_match import Embedder, ScriptTracker, chunk_script

VOSK_MODEL = Path(__file__).resolve().parent.parent / "gap_heuristic" / "vosk-model-small-en-us-0.15"
SR = 16000
UPDATE_MS = 1500            # how often the speech is compared with the script
EXAMPLE = """Good morning everyone, and thank you for coming to our quarterly review.
Today I will walk you through our sales results, our hiring plans, and the new product launch.
First, sales grew by twelve percent compared to last quarter, mostly in the European market.
Our biggest customer, Northwind, renewed their contract for another three years.
Second, we plan to hire fifteen new engineers to support the mobile app team.
Recruiting will focus on senior developers with experience in cloud infrastructure.
Third, the new product called Atlas launches in March with a public beta for early customers.
Atlas helps small businesses track inventory automatically using their phone camera.
To wrap up, the quarter was strong and the team is ready for the next phase.
Thank you, and I am happy to take any questions now."""

STATUS_COLORS = {"on track": "#107557", "jumped ahead": "#b45309", "went back": "#b45309",
                 "checking": "#475569", "stuck": "#b45309", "off topic": "#b91c1c", "ad-libbing": "#7c3aed"}


class Transcriber(threading.Thread):
    """Mic (or a .wav file) -> Vosk -> ("partial" | "final", text) events on a queue."""

    def __init__(self, events, wav=None):
        super().__init__(daemon=True)
        self.events, self.wav = events, wav
        self.stop_flag = threading.Event()

    def run(self):
        from vosk import KaldiRecognizer, Model, SetLogLevel
        SetLogLevel(-1)
        recognizer = KaldiRecognizer(Model(str(VOSK_MODEL)), SR)
        last_partial = ""
        for block in (self.wav_blocks() if self.wav else self.mic_blocks()):
            if self.stop_flag.is_set():
                break
            if recognizer.AcceptWaveform(block):
                text = json.loads(recognizer.Result()).get("text", "")
                if text:
                    self.events.put(("final", text))
                last_partial = ""
            else:
                partial = json.loads(recognizer.PartialResult()).get("partial", "")
                if partial != last_partial:
                    self.events.put(("partial", partial))
                    last_partial = partial
        text = json.loads(recognizer.FinalResult()).get("text", "")
        if text:
            self.events.put(("final", text))
        self.events.put(("stopped", ""))

    def mic_blocks(self):
        import sounddevice as sd
        blocks = queue.Queue()
        with sd.RawInputStream(samplerate=SR, channels=1, dtype="int16", blocksize=4000,
                               callback=lambda data, *_: blocks.put(bytes(data))):
            while not self.stop_flag.is_set():
                try:
                    yield blocks.get(timeout=0.5)
                except queue.Empty:
                    pass

    def wav_blocks(self):
        """A recording, resampled to 16 kHz mono and played at real-time speed."""
        with wave.open(str(self.wav)) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, w.getnchannels())
            rate = w.getframerate()
        mono = pcm.mean(axis=1)
        if rate != SR:
            mono = np.interp(np.arange(0, len(mono), rate / SR), np.arange(len(mono)), mono)
        audio = mono.astype("<i2").tobytes()
        step = 4000 * 2                       # 0.25 s of 16-bit samples
        for i in range(0, len(audio), step):
            yield audio[i:i + step]
            time.sleep(0.25)
        yield bytes(SR)                       # 0.5 s of silence closes the last sentence


class ScriptCompareApp:
    def __init__(self, root, wav=None):
        self.root, self.wav = root, wav
        self.events = queue.Queue()
        self.embed = None
        self.tracker = None
        self.transcriber = None
        self.finals, self.partial = [], ""
        self.last_compared = ""
        self.last_match = None
        root.title("Script compare (test)")
        root.geometry("1400x860")
        self.build()
        threading.Thread(target=self.load_model, daemon=True).start()
        root.after(100, self.poll)
        root.after(UPDATE_MS, self.compare)

    # ---------- layout ----------
    def build(self):
        panes = ttk.PanedWindow(self.root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=8)

        left = ttk.Frame(panes)
        panes.add(left, weight=3)
        ttk.Label(left, text="Script (paste yours, then Chunk script)", font=("", 13, "bold")).pack(anchor="w")
        self.script_box = tk.Text(left, height=9, wrap="word", font=("", 12), undo=True)
        self.script_box.insert("1.0", EXAMPLE)
        self.script_box.pack(fill="x", pady=(4, 6))
        row = ttk.Frame(left)
        row.pack(fill="x")
        self.chunk_button = ttk.Button(row, text="Chunk script", command=self.load_script, state="disabled")
        self.chunk_button.pack(side="left")
        ttk.Button(row, text="Reset position", command=self.reset).pack(side="left", padx=6)
        self.model_status = ttk.Label(row, text="Loading embedding model...")
        self.model_status.pack(side="left", padx=10)

        ttk.Label(left, text="Chunks: score vs what you just said", font=("", 13, "bold")).pack(anchor="w", pady=(10, 0))
        legend = ttk.Frame(left)
        legend.pack(anchor="w")
        for text, color in (("current position", "#bbf7d0"), ("best match now", "#fef08a"),
                            ("tied (within tolerance)", "#e0e7ff"), ("skipped", "#e5e7eb")):
            tk.Label(legend, text=f"  {text}  ", bg=color).pack(side="left", padx=(0, 6), pady=2)
        self.chunk_view = tk.Text(left, wrap="word", font=("", 12), state="disabled", cursor="arrow")
        self.chunk_view.pack(fill="both", expand=True, pady=4)
        self.chunk_view.tag_configure("current", background="#bbf7d0")
        self.chunk_view.tag_configure("best", background="#fef08a")
        self.chunk_view.tag_configure("tied", background="#e0e7ff")
        self.chunk_view.tag_configure("skipped", foreground="#9ca3af", overstrike=True)
        self.chunk_view.tag_configure("meta", font=("Menlo", 11), foreground="#475569")

        right = ttk.Frame(panes)
        panes.add(right, weight=2)
        controls = ttk.Frame(right)
        controls.pack(fill="x")
        self.mic_button = ttk.Button(controls, text="Start listening" if not self.wav else "Play recording",
                                     command=self.toggle_listening)
        self.mic_button.pack(side="left")
        ttk.Button(controls, text="Clear transcript", command=self.clear_transcript).pack(side="left", padx=6)

        self.threshold = tk.DoubleVar(value=0.35)
        self.tolerance = tk.DoubleVar(value=0.05)
        self.slider(right, "Off-script threshold: best score needed to count as on script",
                    self.threshold, 0.10, 0.90)
        self.slider(right, "Match tolerance: how far below the best score still counts as tied",
                    self.tolerance, 0.0, 0.30)
        self.stuck_factor = tk.DoubleVar(value=3.0)
        self.slider(right, "Stuck after: this many times a part's expected speaking time (min 20 s)",
                    self.stuck_factor, 1.5, 8.0)

        self.position_label = ttk.Label(right, text="Position: -", font=("", 20, "bold"))
        self.position_label.pack(anchor="w", pady=(10, 0))
        self.status_label = tk.Label(right, text="Load a script and start talking", font=("", 15, "bold"),
                                     fg="#475569", anchor="w")
        self.status_label.pack(fill="x")
        self.scores_label = ttk.Label(right, text="", font=("Menlo", 11), foreground="#475569")
        self.scores_label.pack(anchor="w")

        ttk.Label(right, text="Live transcript", font=("", 13, "bold")).pack(anchor="w", pady=(10, 0))
        self.transcript_view = tk.Text(right, height=9, wrap="word", font=("", 12), state="disabled")
        self.transcript_view.pack(fill="both", expand=True, pady=4)
        self.transcript_view.tag_configure("partial", foreground="#9ca3af")
        ttk.Label(right, text="Compared with the script (recent words, fillers removed):").pack(anchor="w")
        self.window_label = ttk.Label(right, text="", wraplength=520, foreground="#334155")
        self.window_label.pack(anchor="w", fill="x")

        typed = ttk.Frame(right)
        typed.pack(fill="x", pady=(8, 0))
        ttk.Label(typed, text="Type instead of speaking:").pack(side="left")
        self.typed = ttk.Entry(typed)
        self.typed.pack(side="left", fill="x", expand=True, padx=6)
        self.typed.bind("<Return>", lambda _e: self.send_typed())
        ttk.Button(typed, text="Send", command=self.send_typed).pack(side="left")

        ttk.Label(right, text="Alerts", font=("", 13, "bold")).pack(anchor="w", pady=(10, 0))
        self.alerts = tk.Listbox(right, height=7, font=("", 12))
        self.alerts.pack(fill="both", expand=True)

    def slider(self, parent, text, var, lo, hi):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=(8, 0))
        value = ttk.Label(frame, text=f"{var.get():.2f}", width=5)
        ttk.Label(frame, text=text).pack(anchor="w")
        scale = ttk.Scale(frame, from_=lo, to=hi, variable=var, orient="horizontal",
                          command=lambda _v: (value.configure(text=f"{var.get():.2f}"), self.apply_sliders()))
        scale.pack(side="left", fill="x", expand=True)
        value.pack(side="left", padx=6)

    # ---------- actions ----------
    def load_model(self):
        # runs on a worker thread: Tk isn't thread-safe, so report back through the event queue
        self.events.put(("model", Embedder()))

    def model_ready(self):
        self.model_status.configure(text="Embedding model ready")
        self.chunk_button.configure(state="normal")
        self.load_script()

    def load_script(self):
        chunks = chunk_script(self.script_box.get("1.0", "end"))
        if not chunks or not self.embed:
            return
        self.tracker = ScriptTracker(chunks, self.embed, self.threshold.get(), self.tolerance.get(),
                                     stuck_factor=self.stuck_factor.get())
        self.last_match, self.last_compared = None, ""
        self.model_status.configure(text=f"{len(chunks)} chunks")
        self.render_chunks()
        self.render_status()

    def apply_sliders(self):
        if self.tracker:
            self.tracker.threshold = self.threshold.get()
            self.tracker.tolerance = self.tolerance.get()
            self.tracker.stuck_factor = self.stuck_factor.get()
            self.last_compared = ""                # re-compare now with the new settings

    def reset(self):
        if self.tracker:
            self.tracker.reset()
            self.last_match, self.last_compared = None, ""
            self.render_chunks()
            self.render_status()

    def toggle_listening(self):
        if self.transcriber:
            self.transcriber.stop_flag.set()
            return
        self.transcriber = Transcriber(self.events, self.wav)
        self.transcriber.start()
        self.mic_button.configure(text="Stop")

    def clear_transcript(self):
        self.finals, self.partial = [], ""
        self.render_transcript()

    def send_typed(self):
        text = self.typed.get().strip()
        if text:
            self.finals.append(text.lower())
            self.typed.delete(0, "end")
            self.render_transcript()
            self.compare(reschedule=False)

    # ---------- loops ----------
    def poll(self):
        changed = False
        while not self.events.empty():
            kind, text = self.events.get()          # text is the Embedder for "model"
            if kind == "final":
                self.finals.append(text)
                self.partial = ""
            elif kind == "partial":
                self.partial = text
            elif kind == "model":
                self.embed = text
                self.model_ready()
                continue
            elif kind == "stopped":
                self.transcriber = None
                self.mic_button.configure(text="Start listening" if not self.wav else "Play recording")
            changed = True
        if changed:
            self.render_transcript()
        self.root.after(100, self.poll)

    def compare(self, reschedule=True):
        transcript = " ".join(self.finals + [self.partial])
        if self.tracker and transcript != self.last_compared:
            self.last_compared = transcript
            match = self.tracker.update(transcript)
            if match:
                self.last_match = match
                for event in match.events:
                    self.alerts.insert(0, f"{time.strftime('%H:%M:%S')}  {event['message']}")
                self.render_chunks()
                self.render_status()
        if reschedule:
            self.root.after(UPDATE_MS, self.compare)

    # ---------- drawing ----------
    def render_transcript(self):
        v = self.transcript_view
        v.configure(state="normal")
        v.delete("1.0", "end")
        v.insert("end", " ".join(self.finals) + " ")
        v.insert("end", self.partial, "partial")
        v.see("end")
        v.configure(state="disabled")

    def render_chunks(self):
        t, m = self.tracker, self.last_match
        v = self.chunk_view
        v.configure(state="normal")
        v.delete("1.0", "end")
        for i, chunk in enumerate(t.chunks):
            tags = []
            if m is not None:
                best = m.score[m.best]
                gap = m.score[i] - best
                bar = "█" * round(max(m.score[i], 0) * 20)
                meta = (f"{i + 1:>2}  {bar:<20} score {m.score[i]:.2f}  (emb {m.similarity[i]:.2f}, "
                        f"keywords {m.keyword[i]:.2f})  {'best' if i == m.best else f'{gap:+.2f} vs best'}\n")
                if i == m.best:
                    tags.append("best")
                elif gap >= -t.tolerance:
                    tags.append("tied")
            else:
                meta = f"{i + 1:>2}\n"
            if i == t.position:
                tags = ["current"]
            if i in t.skipped:
                tags.append("skipped")
            start = v.index("end-1c")
            v.insert("end", meta, ("meta",))
            v.insert("end", chunk + "\n\n")
            for tag in tags:
                v.tag_add(tag, start, v.index("end-2c"))
        if t.position >= 0:
            v.see(f"{t.position * 3 + 1}.0")
        v.configure(state="disabled")

    def render_status(self):
        t, m = self.tracker, self.last_match
        if not t:
            return
        self.position_label.configure(
            text=f"Position: chunk {t.position + 1} / {len(t.chunks)}" if t.position >= 0 else
            f"Position: not started ({len(t.chunks)} chunks)")
        if m is None:
            self.status_label.configure(text="Waiting for speech", fg="#475569")
            self.scores_label.configure(text="")
            self.window_label.configure(text="")
            return
        color = next((c for k, c in STATUS_COLORS.items() if m.status.startswith(k)), "#475569")
        skipped = f"   skipped so far: {', '.join(str(i + 1) for i in sorted(t.skipped))}" if t.skipped else ""
        self.status_label.configure(text=m.status.capitalize() + skipped, fg=color)
        self.scores_label.configure(
            text=f"best chunk {m.best + 1} score {m.score[m.best]:.2f}  |  chosen {m.chosen + 1}  |  "
                 f"whole-script similarity {m.topic:.2f}  |  threshold {t.threshold:.2f}")
        self.window_label.configure(text=m.window)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", type=Path, help="play this recording instead of using the microphone")
    args = parser.parse_args()
    root = tk.Tk()
    ScriptCompareApp(root, args.wav)
    root.mainloop()


if __name__ == "__main__":
    main()
