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
  private ws: WebSocket | null = null
  private closed = false
  private retry: ReturnType<typeof setTimeout> | null = null

  constructor(private url = audioAnalysisUrl()) {
    this.connect()
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }

  /** Raw 16 kHz float32 samples. Returns false if dropped (not connected, or Python is behind). */
  sendAudio(samples: Float32Array): boolean {
    const ws = this.ws
    if (!ws || ws.readyState !== WebSocket.OPEN || ws.bufferedAmount > MAX_BUFFERED_BYTES)
      return false
    ws.send(samples)
    return true
  }

  send(msg: AudioControlMessage): void {
    if (this.connected) this.ws!.send(JSON.stringify(msg))
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
    ws.onopen = () => this.onConnectionChange?.(true)
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
