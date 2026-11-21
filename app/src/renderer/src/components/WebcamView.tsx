// Webcam + overlay. Each new camera frame: run MediaPipe, send landmarks to Python, log them if
// recording, then redraw the skeleton and Python's latest overlay state on the canvas.

import { useEffect, useImperativeHandle, useRef } from 'react'

import { WebcamAnalysisClient } from '../webcam/analysisClient'
import { drawLandmarks, drawOverlay } from '../webcam/draw'
import { createLandmarkers, detect, type Landmarkers } from '../webcam/landmarkers'
import type { ClientMessage, StateMessage } from '../webcam/protocol'

export interface WebcamStatus {
  ready: boolean // models loaded and camera running
  error: string | null
  connected: boolean // Python analysis service
  calibration: StateMessage['calibration'] | null
}

export interface WebcamHandle {
  /** The camera (+ mic) stream, once running. */
  stream(): MediaStream | null
  /** Sends a message to Python and to the logger (the session recording), if set. */
  send(msg: ClientMessage): void
  setLogger(log: ((msg: ClientMessage) => void) | null): void
}

interface Props {
  ref?: React.Ref<WebcamHandle>
  onStatus: (s: WebcamStatus) => void
}

async function openCamera(): Promise<MediaStream> {
  const video = { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } }
  try {
    return await navigator.mediaDevices.getUserMedia({ video, audio: true })
  } catch {
    // no mic (or mic permission denied): video still works, recordings just have no sound
    return navigator.mediaDevices.getUserMedia({ video })
  }
}

export function WebcamView({ ref, onStatus }: Props): React.JSX.Element {
  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const clientRef = useRef<WebcamAnalysisClient | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const loggerRef = useRef<((msg: ClientMessage) => void) | null>(null)

  useImperativeHandle(
    ref,
    () => ({
      stream: () => streamRef.current,
      send: (msg) => {
        clientRef.current?.send(msg)
        loggerRef.current?.(msg)
      },
      setLogger: (log) => {
        loggerRef.current = log
      }
    }),
    []
  )

  useEffect(() => {
    const video = videoRef.current!
    const canvas = canvasRef.current!
    let cancelled = false
    let landmarkers: Landmarkers | null = null
    let frameRequest = 0
    const client = new WebcamAnalysisClient()
    clientRef.current = client

    let lastT = 0
    let fps = 0
    let lastStatus = ''
    // only re-render React when something visible changed (remaining time to 0.1 s)
    const report = (s: WebcamStatus): void => {
      const key = JSON.stringify({
        ...s,
        calibration: s.calibration && {
          ...s.calibration,
          remaining: Math.ceil(s.calibration.remaining * 10)
        }
      })
      if (key !== lastStatus) {
        lastStatus = key
        onStatus(s)
      }
    }

    async function start(): Promise<void> {
      try {
        const [lm, cam] = await Promise.all([createLandmarkers(), openCamera()])
        if (cancelled) {
          lm.close()
          cam.getTracks().forEach((track) => track.stop())
          return
        }
        landmarkers = lm
        streamRef.current = cam
        video.srcObject = cam
        await video.play()
        frameRequest = video.requestVideoFrameCallback(onFrame)
      } catch (err) {
        report({ ready: false, error: String(err), connected: false, calibration: null })
      }
    }

    function onFrame(): void {
      if (cancelled || !landmarkers) return
      // MediaPipe needs strictly increasing timestamps
      const t = Math.max(performance.now() / 1000, lastT + 0.001)
      if (lastT) fps = 0.9 * fps + 0.1 / Math.max(t - lastT, 1e-6)
      lastT = t

      const frame = detect(landmarkers, video, t)
      client.send(frame)
      loggerRef.current?.(frame)

      if (canvas.width !== frame.width || canvas.height !== frame.height) {
        canvas.width = frame.width
        canvas.height = frame.height
      }
      const ctx = canvas.getContext('2d')!
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      drawLandmarks(ctx, frame)
      drawOverlay(ctx, client.latest, {
        fps,
        handsSeen: frame.hands.length,
        connected: client.connected
      })

      report({
        ready: true,
        error: null,
        connected: client.connected,
        calibration: client.latest?.calibration ?? null
      })
      frameRequest = video.requestVideoFrameCallback(onFrame)
    }

    start()
    return () => {
      cancelled = true
      video.cancelVideoFrameCallback(frameRequest)
      client.close()
      clientRef.current = null
      landmarkers?.close()
      streamRef.current?.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }
  }, [onStatus])

  return (
    <div className="webcam">
      {/* the video is mirrored with CSS; landmarks are mirrored in landmarkers.ts, so the canvas isn't */}
      <video ref={videoRef} className="webcam-video" muted playsInline />
      <canvas ref={canvasRef} className="webcam-overlay" />
    </div>
  )
}
