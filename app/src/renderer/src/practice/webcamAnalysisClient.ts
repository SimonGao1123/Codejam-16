// WebSocket client for the Python analysis service. Reconnects on its own, and drops frames
// instead of queueing them when Python falls behind, so the overlay never lags further and further.

import { webcamAnalysisUrl, type ClientMessage, type StateMessage } from './webcamProtocol'

const RECONNECT_MS = 1000
const MAX_BUFFERED_BYTES = 256 * 1024 // ~10 frames; beyond this, skip sending until Python catches up

export class WebcamAnalysisClient {
  latest: StateMessage | null = null
  private ws: WebSocket | null = null
  private closed = false
  private retry: ReturnType<typeof setTimeout> | null = null

  constructor(private url = webcamAnalysisUrl()) {
    this.connect()
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }

  /** Returns false if the message was dropped (not connected, or Python is behind). */
  send(msg: ClientMessage): boolean {
    const ws = this.ws
    if (!ws || ws.readyState !== WebSocket.OPEN) return false
    if (msg.type === 'frame' && ws.bufferedAmount > MAX_BUFFERED_BYTES) return false
    ws.send(JSON.stringify(msg))
    return true
  }

  close(): void {
    this.closed = true
    if (this.retry) clearTimeout(this.retry)
    this.ws?.close()
  }

  private connect(): void {
    const ws = new WebSocket(this.url)
    this.ws = ws
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'state') this.latest = msg
      } catch (err) {
        console.warn('bad message from analysis service', err)
      }
    }
    ws.onclose = () => {
      this.latest = null
      if (!this.closed) this.retry = setTimeout(() => this.connect(), RECONNECT_MS)
    }
  }
}
