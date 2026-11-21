// Records the webcam: the video (+ mic track) as .webm, and every message sent to the webcam
// analysis service as .jsonl, so a session can be replayed into Python later. Files are written by the
// main process (see src/main/webcamRecorder.ts), streamed so long sessions don't sit in memory.

import type { ClientMessage } from './webcamProtocol'

const CHUNK_MS = 1000

export class WebcamRecorder {
  private recorder: MediaRecorder | null = null
  private pending: Promise<void> = Promise.resolve()
  private stopped: Promise<void> = Promise.resolve()

  get active(): boolean {
    return this.recorder !== null
  }

  /** Starts recording `stream`; resolves with the folder + base name the files are saved under. */
  async start(stream: MediaStream, t: number): Promise<string> {
    const { base } = await window.api.webcamRecorder.start()
    const mimeType = [
      'video/webm;codecs=vp9,opus',
      'video/webm;codecs=vp8,opus',
      'video/webm'
    ].find((m) => MediaRecorder.isTypeSupported(m))
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
    recorder.ondataavailable = (e) => {
      // keep chunks in order even though arrayBuffer() is async
      this.pending = this.pending.then(async () => {
        if (e.data.size) window.api.webcamRecorder.chunk(new Uint8Array(await e.data.arrayBuffer()))
      })
    }
    // first line ties the .jsonl clock (t) to the start of the video
    window.api.webcamRecorder.line(JSON.stringify({ type: 'recording', t, video: `${base}.webm` }))
    // resolves however the recorder stops: stop() below, or by itself when the camera tracks end
    this.stopped = new Promise((resolve) => (recorder.onstop = () => resolve()))
    recorder.start(CHUNK_MS)
    this.recorder = recorder
    return base
  }

  log(msg: ClientMessage): void {
    if (this.recorder) window.api.webcamRecorder.line(JSON.stringify(msg))
  }

  /** Stops and finishes writing; resolves with the saved file paths. */
  async stop(): Promise<{ video: string; data: string } | null> {
    const recorder = this.recorder
    if (!recorder) return null
    this.recorder = null
    if (recorder.state !== 'inactive') recorder.stop()
    await this.stopped
    await this.pending
    return window.api.webcamRecorder.stop()
  }
}
