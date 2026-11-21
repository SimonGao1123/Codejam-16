// Webcam video + analysis overlay for the practice session's camera stream. Models load and the
// Python connection opens as soon as this mounts; while `stream` is set, every new camera frame
// runs MediaPipe, is sent to Python (and the logger, when recording), and the skeleton plus
// Python's latest overlay state are redrawn on the canvas.

import { useEffect, useImperativeHandle, useRef } from 'react'

import { useAlertPreferences } from '../../liveAlert/useAlertPreferences'
import { WebcamAnalysisClient } from '../camera/webcamAnalysisClient'
import { drawLandmarks, drawOverlay } from '../camera/webcamDraw'
import { createLandmarkers, detect, type Landmarkers } from '../camera/webcamLandmarkers'
import type { ClientMessage, StateMessage } from '../camera/webcamProtocol'

export interface WebcamStatus {
  ready: boolean // models loaded and camera frames being analysed
  error: string | null
  connected: boolean // Python webcam analysis service
  calibration: StateMessage['calibration'] | null
}

export interface WebcamHandle {
  /** Sends a message to Python and to the logger (the webcam recording), if set. */
  send(msg: ClientMessage): void
  setLogger(log: ((msg: ClientMessage) => void) | null): void
}

interface Props {
  ref?: React.Ref<WebcamHandle>
  stream: MediaStream | null
  wpm: number | null
  wpmUpdatedAt: number | null
  avgWpm: number | null // last 3 finished lines
  desiredWpm: number | null // the pace to aim for
  onStatus: (s: WebcamStatus) => void
}

const STATUS_MS = 100

export default function WebcamView({
  ref,
  stream,
  wpm,
  wpmUpdatedAt,
  avgWpm,
  desiredWpm,
  onStatus
}: Props): React.JSX.Element {
  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const clientRef = useRef<WebcamAnalysisClient | null>(null)
  const landmarkersRef = useRef<Landmarkers | null>(null)
  const loggerRef = useRef<((msg: ClientMessage) => void) | null>(null)
  const analysingRef = useRef(false)
  const errorRef = useRef<string | null>(null)
  const lastTRef = useRef(0) // MediaPipe needs strictly increasing timestamps, across sessions too
  const wpmRef = useRef(wpm)
  const wpmUpdatedAtRef = useRef(wpmUpdatedAt)
  const paceRef = useRef({ avgWpm, desiredWpm })
  // Live alerts tab settings: rules go to Python, the style decides how alerts are drawn
  const [preferences] = useAlertPreferences()
  const preferencesRef = useRef(preferences)

  useEffect(() => {
    wpmRef.current = wpm
  }, [wpm])
  useEffect(() => {
    wpmUpdatedAtRef.current = wpmUpdatedAt
  }, [wpmUpdatedAt])
  useEffect(() => {
    paceRef.current = { avgWpm, desiredWpm }
  }, [avgWpm, desiredWpm])

  useImperativeHandle(
    ref,
    () => ({
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

  // models + Python connection: for as long as the practice page is open
  useEffect(() => {
    let cancelled = false
    const client = new WebcamAnalysisClient()
    clientRef.current = client
    client.setSettings({ type: 'settings', rules: preferencesRef.current.rules })
    createLandmarkers().then(
      (lm) => {
        if (cancelled) lm.close()
        else landmarkersRef.current = lm
      },
      (err) => (errorRef.current = `Couldn't load the tracking models: ${err}`)
    )

    // report status a few times a second; React only re-renders when something visible changed
    let last = ''
    const timer = window.setInterval(() => {
      const s: WebcamStatus = {
        ready: analysingRef.current,
        error: errorRef.current,
        connected: client.connected,
        calibration: client.latest?.calibration ?? null
      }
      const key = JSON.stringify({
        ...s,
        calibration: s.calibration && {
          ...s.calibration,
          remaining: Math.ceil(s.calibration.remaining * 10)
        }
      })
      if (key !== last) {
        last = key
        onStatus(s)
      }
    }, STATUS_MS)

    return () => {
      cancelled = true
      window.clearInterval(timer)
      client.close()
      clientRef.current = null
      landmarkersRef.current?.close()
      landmarkersRef.current = null
    }
  }, [onStatus])

  // Live alerts changes: tell Python right away (the client also re-sends them after a reconnect)
  useEffect(() => {
    preferencesRef.current = preferences
    clientRef.current?.setSettings({ type: 'settings', rules: preferences.rules })
  }, [preferences])

  // per-frame analysis: while the session's camera stream is on
  useEffect(() => {
    const video = videoRef.current!
    const canvas = canvasRef.current!
    if (!stream) return
    let stopped = false
    let frameRequest = 0
    let fps = 0

    function onFrame(): void {
      if (stopped) return
      frameRequest = video.requestVideoFrameCallback(onFrame)
      const landmarkers = landmarkersRef.current
      const client = clientRef.current
      if (!landmarkers || !client) return // models still loading

      const t = Math.max(performance.now() / 1000, lastTRef.current + 0.001)
      const dt = t - lastTRef.current
      if (dt < 1) fps = 0.9 * fps + 0.1 / Math.max(dt, 1e-6)
      lastTRef.current = t

      const frame = detect(landmarkers, video, t)
      client.send(frame)
      loggerRef.current?.(frame)
      analysingRef.current = true

      if (canvas.width !== frame.width || canvas.height !== frame.height) {
        canvas.width = frame.width
        canvas.height = frame.height
      }
      const ctx = canvas.getContext('2d')!
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      drawLandmarks(ctx, frame)
      drawOverlay(
        ctx,
        client.latest,
        {
          fps,
          handsSeen: frame.hands.length,
          connected: client.connected,
          wpm: wpmRef.current,
          wpmUpdatedAt: wpmUpdatedAtRef.current,
          ...paceRef.current
        },
        preferencesRef.current.style
      )
    }

    video.srcObject = stream
    video.play().then(
      () => {
        if (!stopped) frameRequest = video.requestVideoFrameCallback(onFrame)
      },
      () => {} // interrupted because the session ended before playback started
    )

    return () => {
      stopped = true
      analysingRef.current = false
      video.cancelVideoFrameCallback(frameRequest)
      video.srcObject = null
      canvas.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height)
    }
  }, [stream])

  return (
    <>
      {/* the video is mirrored with CSS; landmarks are mirrored in webcamLandmarkers.ts, so the canvas isn't */}
      <video ref={videoRef} muted playsInline hidden={!stream} aria-label="Live camera preview" />
      <canvas ref={canvasRef} hidden={!stream} aria-hidden="true" />
    </>
  )
}
