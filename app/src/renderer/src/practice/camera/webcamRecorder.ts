// Records the webcam: the video (+ mic track) as .webm, and every message sent to the webcam
// analysis service as .jsonl, so a session can be replayed into Python later. Files are written by the
// main process (see src/main/webcamRecorder.ts), streamed so long sessions don't sit in memory.

import type { ClientMessage } from './webcamProtocol'

const CHUNK_MS = 1000

export class WebcamRecorder {
  private recorder: MediaRecorder | null = null
  private pending: Promise<void> = Promise.resolve()
  private stopped: Promise<void> = Promise.resolve()

  /** Starts recording and returns its start time on the renderer's monotonic clock. */
  async start(stream: MediaStream): Promise<number> {
    const formats = stream.getVideoTracks().length
      ? ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm']
      : ['audio/webm;codecs=opus', 'audio/webm']
    const mimeType = formats.find((m) => MediaRecorder.isTypeSupported(m))
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
    const { base } = await window.api.webcamRecorder.start()
    recorder.ondataavailable = (e) => {
      // keep chunks in order even though arrayBuffer() is async
      this.pending = this.pending.then(async () => {
        if (e.data.size) window.api.webcamRecorder.chunk(new Uint8Array(await e.data.arrayBuffer()))
      })
    }
    // resolves however the recorder stops: stop() below, or by itself when the camera tracks end
    this.stopped = new Promise((resolve) => (recorder.onstop = () => resolve()))
    const t = performance.now() / 1000
    try {
      recorder.start(CHUNK_MS)
    } catch (error) {
      await window.api.webcamRecorder.stop()
      throw error
    }
    window.api.webcamRecorder.line(JSON.stringify({ type: 'recording', t, video: `${base}.webm` }))
    this.recorder = recorder
    return t
  }

  log(msg: ClientMessage): void {
    if (this.recorder) window.api.webcamRecorder.line(JSON.stringify(msg))
  }

  /** Stops and finishes writing; resolves with the saved file paths (null if discarded). */
  async stop(
    discard = false
  ): Promise<{ video: string; data: string; playbackUrl: string } | null> {
    const recorder = this.recorder
    if (!recorder) return null
    this.recorder = null
    if (recorder.state !== 'inactive') recorder.stop()
    await this.stopped
    try {
      await this.pending
    } catch (error) {
      await window.api.webcamRecorder.stop(discard)
      throw error
    }
    return window.api.webcamRecorder.stop(discard)
  }
}
