// WebSocket client for the Python service's audio side. Reconnects on its own; drops audio
// instead of queueing it if Python falls behind, so the transcript doesn't lag further and further.

import {
  audioAnalysisUrl,
  type AudioControlMessage,
  type AudioServerMessage
} from './audioProtocol'

const RECONNECT_MS = 1000
const MAX_BUFFERED_BYTES = 512 * 1024 // ~8 s of audio

export class AudioAnalysisClient {
  onMessage: ((msg: AudioServerMessage) => void) | null = null
  onConnectionChange: ((connected: boolean) => void) | null = null
  private script: string | null = null
  private targetSeconds: number | null = null
  private ws: WebSocket | null = null
  private closed = false
  private retry: ReturnType<typeof setTimeout> | null = null

  constructor(private url = audioAnalysisUrl()) {
    this.connect()
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }

  /** Timestamp followed by 16 kHz float32 samples. Returns false if dropped. */
  sendAudio(samples: Float32Array, startedAt: number): boolean {
    const ws = this.ws
    if (!ws || ws.readyState !== WebSocket.OPEN || ws.bufferedAmount > MAX_BUFFERED_BYTES)
      return false
    const frame = new ArrayBuffer(Float64Array.BYTES_PER_ELEMENT + samples.byteLength)
    new DataView(frame).setFloat64(0, startedAt, true)
    new Float32Array(frame, Float64Array.BYTES_PER_ELEMENT).set(samples)
    ws.send(frame)
    return true
  }

  send(msg: AudioControlMessage): void {
    if (this.connected) this.ws!.send(JSON.stringify(msg))
  }

  /** Sends the script now if connected, and again after every reconnect. */
  setScript(text: string | null): void {
    this.script = text
    this.send({ type: 'script', text })
  }

  /** Sends the time the user has now if connected, and again after every reconnect. */
  setTargetTime(seconds: number | null): void {
    this.targetSeconds = seconds
    this.send({ type: 'target_time', seconds })
  }

  close(): void {
    this.closed = true
    if (this.retry) clearTimeout(this.retry)
    this.ws?.close()
  }

  private connect(): void {
    const ws = new WebSocket(this.url)
    ws.binaryType = 'arraybuffer'
    this.ws = ws
    ws.onopen = () => {
      this.send({ type: 'script', text: this.script }) // a restarted service has no script
      this.send({ type: 'target_time', seconds: this.targetSeconds })
      this.onConnectionChange?.(true)
    }
    ws.onmessage = (e) => {
      try {
        this.onMessage?.(JSON.parse(e.data))
      } catch (err) {
        console.warn('bad message from audio analysis service', err)
      }
    }
    ws.onclose = () => {
      this.onConnectionChange?.(false)
      if (!this.closed) this.retry = setTimeout(() => this.connect(), RECONNECT_MS)
    }
  }
}
