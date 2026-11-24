// WebSocket client for the Python analysis service. Reconnects on its own, and drops frames
// instead of queueing them when Python falls behind, so the overlay never lags further and further.
// (Frames are small, so the socket's own buffers would hold seconds of them: replies are counted instead.)

import {
  webcamAnalysisUrl,
  type ClientMessage,
  type ServerMessage,
  type SettingsMessage,
  type StateMessage
} from './webcamProtocol'

const RECONNECT_MS = 1000
const MAX_IN_FLIGHT = 3 // frames sent but not answered yet; beyond this, skip sending until Python catches up

export class WebcamAnalysisClient {
  latest: StateMessage | null = null
  private settings: SettingsMessage | null = null
  private ws: WebSocket | null = null
  private inFlight = 0
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
    if (msg.type === 'frame') {
      if (this.inFlight >= MAX_IN_FLIGHT) return false
      this.inFlight++
    }
    ws.send(JSON.stringify(msg))
    return true
  }

  /** Sends the alert settings now if connected, and again after every reconnect. */
  setSettings(msg: SettingsMessage): void {
    this.settings = msg
    this.send(msg)
  }

  close(): void {
    this.closed = true
    if (this.retry) clearTimeout(this.retry)
    this.ws?.close()
  }

  private connect(): void {
    const ws = new WebSocket(this.url)
    this.ws = ws
    ws.onopen = () => {
      this.inFlight = 0
      if (this.settings) this.send(this.settings) // a restarted service starts with defaults
    }
    ws.onmessage = (e) => {
      try {
        const msg: ServerMessage = JSON.parse(e.data)
        if (msg.type !== 'state') return
        this.inFlight--
        this.latest = { ...this.latest, ...msg } as StateMessage // the first reply has every field
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
