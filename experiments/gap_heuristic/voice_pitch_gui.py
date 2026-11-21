"""Desktop interface for the live voice monitor."""

from collections import deque
import tkinter as tk
from tkinter import ttk

import sounddevice as sd

from voice_pitch import BLOCK_SECONDS, BLOCK_SIZE, SAMPLE_RATE, VoiceMonitor


CALIBRATION_VOICED_SECONDS = 3
CALIBRATION_TIMEOUT_SECONDS = 10
GRAPH_WINDOW_SECONDS = 15
GRAPH_WINDOW_BLOCKS = round(GRAPH_WINDOW_SECONDS / BLOCK_SECONDS)
PITCH_JOIN_BLOCKS = round(0.2 / BLOCK_SECONDS)


class VoicePitchWindow:
    def __init__(self, root):
        self.root = root
        self.stream = None
        self.poll_id = None
        self.monitor = VoiceMonitor()
        self.blocks_read = 0
        self.calibration_readings = []
        self.calibration_started = None
        self.pitch_history = deque(maxlen=GRAPH_WINDOW_BLOCKS + 1)
        self.volume_history = deque(maxlen=GRAPH_WINDOW_BLOCKS + 1)

        root.title("Voice Check")
        root.minsize(500, 620)
        root.protocol("WM_DELETE_WINDOW", self.close)

        panel = ttk.Frame(root, padding=24)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Voice Check", font=("TkDefaultFont", 20, "bold")).pack(anchor="w")
        self.status = tk.StringVar(value="Start the microphone to check your speaking level.")
        ttk.Label(panel, textvariable=self.status, wraplength=390).pack(anchor="w", pady=(4, 16))

        readings = ttk.Frame(panel)
        readings.pack(fill="x", pady=(0, 16))
        readings.columnconfigure((0, 1), weight=1)
        self.pitch = tk.StringVar(value="—")
        self.volume = tk.StringVar(value="—")
        for column, (label, value) in enumerate((("Pitch", self.pitch), ("Volume", self.volume))):
            ttk.Label(readings, text=label).grid(row=0, column=column, sticky="w")
            ttk.Label(readings, textvariable=value, font=("TkDefaultFont", 16, "bold")).grid(
                row=1, column=column, sticky="w", pady=(3, 0)
            )
        self.meter = ttk.Progressbar(readings, maximum=60)
        self.meter.grid(row=2, column=1, sticky="ew", pady=(6, 0))

        controls = ttk.Frame(panel)
        controls.pack(fill="x")
        self.button = ttk.Button(controls, text="Start microphone", command=self.start)
        self.button.pack(side="left")
        self.calibrate_button = ttk.Button(
            controls, text="Calibrate normal voice", command=self.start_calibration, state="disabled"
        )
        self.calibrate_button.pack(side="left", padx=(8, 0))

        ttk.Label(panel, text="Pitch over time").pack(anchor="w", pady=(16, 4))
        self.graph = tk.Canvas(panel, height=155, background="#f8fafc", highlightthickness=0)
        self.graph.pack(fill="x")
        self.graph.bind("<Configure>", self.draw_pitch_graph)

        ttk.Label(panel, text="Volume over time").pack(anchor="w", pady=(12, 4))
        self.volume_graph = tk.Canvas(panel, height=155, background="#f8fafc", highlightthickness=0)
        self.volume_graph.pack(fill="x")
        self.volume_graph.bind("<Configure>", self.draw_volume_graph)

        ttk.Label(panel, text="Session alerts").pack(anchor="w", pady=(16, 4))
        self.alerts = tk.Listbox(panel, height=5)
        self.alerts.pack(fill="both", expand=True)

    def start(self):
        try:
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=BLOCK_SIZE
            )
            self.stream.start()
        except sd.PortAudioError as error:
            self.stop(f"Microphone unavailable. {error}")
            return

        self.monitor = VoiceMonitor()
        self.blocks_read = 0
        self.pitch_history.clear()
        self.volume_history.clear()
        self.draw_pitch_graph()
        self.draw_volume_graph()
        self.alerts.delete(0, tk.END)
        self.status.set("Listening. Speak at your normal level.")
        self.button.configure(text="Stop microphone", command=self.stop)
        self.calibrate_button.configure(state="normal")
        self.poll()

    def start_calibration(self):
        self.calibration_readings.clear()
        self.calibration_started = self.blocks_read
        self.calibrate_button.configure(state="disabled")
        self.status.set("Speak a few sentences in your normal voice.")

    def collect_calibration(self, samples):
        pitch, volume = self.monitor.measure(samples)
        if pitch is not None:
            self.calibration_readings.append((pitch, volume))

        voiced_seconds = len(self.calibration_readings) * BLOCK_SECONDS
        elapsed = (self.blocks_read - self.calibration_started) * BLOCK_SECONDS
        if voiced_seconds >= CALIBRATION_VOICED_SECONDS:
            pitch, volume = self.monitor.calibrate(self.calibration_readings)
            self.finish_calibration(f"Calibrated. Normal voice: {pitch:.0f} Hz, {volume:.0f} dBFS.")
        elif elapsed >= CALIBRATION_TIMEOUT_SECONDS:
            self.finish_calibration("Too little voiced audio to calibrate. Try again.")
        else:
            self.status.set(
                f"Speak at your normal pitch and volume. "
                f"{voiced_seconds:.1f} / {CALIBRATION_VOICED_SECONDS} seconds captured."
            )

    def finish_calibration(self, message):
        self.calibration_started = None
        self.calibration_readings.clear()
        self.monitor.reset_pending_alerts()
        self.calibrate_button.configure(state="normal")
        self.status.set(message)

    def draw_pitch_graph(self, _event=None):
        baseline = self.monitor.calibrated_baseline
        self.draw_graph(self.graph, self.pitch_history, (50, 100, 200, 300, 400, 500),
                        "Hz", "#2458bd", baseline[0] if baseline else None, PITCH_JOIN_BLOCKS)

    def draw_volume_graph(self, _event=None):
        baseline = self.monitor.calibrated_baseline
        self.draw_graph(self.volume_graph, self.volume_history, (-60, -45, -30, -15, 0),
                        "dBFS", "#864ba7", baseline[1] if baseline else None, 1)

    def draw_graph(self, graph, history, ticks, unit, color, baseline, join_blocks):
        graph.delete("all")
        width, height = graph.winfo_width(), graph.winfo_height()
        left, right = 70, width - 22
        top, bottom = 16, height - 40
        if right <= left or bottom <= top:
            return

        end_seconds = max(GRAPH_WINDOW_SECONDS, self.blocks_read * BLOCK_SECONDS)
        start_seconds = end_seconds - GRAPH_WINDOW_SECONDS

        def x(seconds):
            return left + (seconds - start_seconds) / GRAPH_WINDOW_SECONDS * (right - left)

        def y(value):
            level = min(ticks[-1], max(ticks[0], value))
            return bottom - (level - ticks[0]) / (ticks[-1] - ticks[0]) * (bottom - top)

        for value in ticks:
            graph.create_line(left, y(value), right, y(value), fill="#dbe4ee")
            graph.create_text(left - 6, y(value), text=f"{value} {unit}", anchor="e", fill="#334155")
        first_tick = int(start_seconds // 5) * 5
        for seconds in range(first_tick, int(end_seconds) + 1, 5):
            if seconds >= start_seconds:
                graph.create_line(x(seconds), top, x(seconds), bottom, fill="#dbe4ee")
                graph.create_text(x(seconds), bottom + 12, text=f"{seconds}s", fill="#334155")
        graph.create_text((left + right) / 2, height - 10,
                          text="Elapsed time", fill="#334155")

        if baseline is not None:
            graph.create_line(left, y(baseline), right, y(baseline),
                              fill="#107557", dash=(5, 3))
            graph.create_text(right - 4, max(y(baseline) - 3, top + 10),
                              text=f"Calibrated {baseline:.0f} {unit}",
                              anchor="se", fill="#107557")

        trace = []
        previous_block = None
        for block, value in history:
            if value is None:
                continue
            if previous_block is not None and block - previous_block > join_blocks:
                self.draw_trace(graph, trace, color)
                trace = []
            trace.extend((x(block * BLOCK_SECONDS), y(value)))
            previous_block = block
        self.draw_trace(graph, trace, color)

    def draw_trace(self, graph, trace, color):
        if len(trace) >= 4:
            graph.create_line(*trace, fill=color, width=2.5,
                                   capstyle=tk.ROUND, joinstyle=tk.ROUND)
        elif trace:
            x, y = trace
            graph.create_oval(x - 2, y - 2, x + 2, y + 2, fill=color, outline="")

    def poll(self):
        self.poll_id = None
        try:
            # Read only complete blocks already buffered by PortAudio so Tk stays responsive.
            ready = min(self.stream.read_available // BLOCK_SIZE, 5)
            for _ in range(ready):
                samples, overflow = self.stream.read(BLOCK_SIZE)
                self.blocks_read += 1
                elapsed = self.blocks_read * BLOCK_SECONDS
                if overflow:
                    self.monitor.reset_pending_alerts()
                if self.calibration_started is not None:
                    self.collect_calibration(samples[:, 0])
                else:
                    for alert in self.monitor.process(samples[:, 0], elapsed):
                        self.alerts.insert(tk.END, f"{elapsed:.1f}s  {alert}")
                        self.alerts.see(tk.END)
                self.pitch_history.append((self.blocks_read, self.monitor.latest_pitch_hz))
                self.volume_history.append((self.blocks_read, self.monitor.latest_volume_dbfs))
        except sd.PortAudioError as error:
            self.stop(f"Microphone input failed. {error}")
            return

        if ready:
            pitch = self.monitor.latest_pitch_hz
            volume = self.monitor.latest_volume_dbfs
            self.pitch.set("—" if pitch is None else f"{pitch:.0f} Hz")
            self.volume.set(f"{volume:.0f} dBFS")
            self.meter.configure(value=max(0, min(60, volume + 60)))
            self.draw_pitch_graph()
            self.draw_volume_graph()
        self.poll_id = self.root.after(25, self.poll)

    def stop(self, message="Microphone stopped."):
        if self.poll_id is not None:
            self.root.after_cancel(self.poll_id)
            self.poll_id = None
        if self.stream is not None:
            try:
                self.stream.close()
            except sd.PortAudioError as error:
                message = f"Microphone close failed. {error}"
            finally:
                self.stream = None
        self.pitch.set("—")
        self.volume.set("—")
        self.meter.configure(value=0)
        self.calibration_started = None
        self.calibration_readings.clear()
        self.calibrate_button.configure(state="disabled")
        self.status.set(message)
        self.button.configure(text="Start microphone", command=self.start)

    def close(self):
        self.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    VoicePitchWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
