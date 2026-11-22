import { useCallback, useEffect, useEffectEvent, useRef, useState } from 'react'
import { WebcamRecorder } from './camera/webcamRecorder'
import type { ClientMessage } from './camera/webcamProtocol'

const COUNTDOWN_SECONDS = 3

export interface PracticeSessionState {
  camera: boolean
  setCamera: React.Dispatch<React.SetStateAction<boolean>>
  microphone: boolean
  setMicrophone: React.Dispatch<React.SetStateAction<boolean>>
  running: boolean
  startedAt: number | null
  seconds: number
  finished: boolean
  pending: boolean
  preparing: boolean
  countdown: number | null
  waitingForCalibration: boolean
  saving: boolean
  playbackUrl: string | null
  recordingDuration: number
  recordingError: string
  logWebcam: (message: ClientMessage) => void
  deviceError: string
  cameraStream: MediaStream | null
  micStream: MediaStream | null
  startPractice: () => Promise<void>
  endPractice: () => ReturnType<WebcamRecorder['stop']>
  cancelPractice: () => void
}

/** Zooms the camera all the way out, for the widest view its lens allows, if it supports zoom
 *  (some webcams start zoomed in). Otherwise nothing changes. */
function widestView(stream: MediaStream): void {
  const track = stream.getVideoTracks()[0]
  const zoom = (track?.getCapabilities?.() as { zoom?: { min: number } } | undefined)?.zoom
  if (!zoom) return
  const widest = { zoom: zoom.min } as MediaTrackConstraintSet
  track.applyConstraints({ advanced: [widest] }).catch((err) => console.warn('camera zoom', err))
}

/** `calibrated`: this session's posture calibration has finished (from WebcamControls). With the
 *  camera on, the session (timer, recording, analysis, scoring) only starts once it has, after the
 *  get-ready countdown; the two run at the same time. */
export function usePracticeSession(calibrated: boolean): PracticeSessionState {
  const [camera, setCamera] = useState(true)
  const [microphone, setMicrophone] = useState(true)
  const [started, setStarted] = useState<number | null>(null)
  const [seconds, setSeconds] = useState(0)
  const [finished, setFinished] = useState(false)
  const [waitingForCalibration, setWaitingForCalibration] = useState(false) // countdown done
  const [pending, setPending] = useState(false)
  const [countdown, setCountdown] = useState<number | null>(null)
  const [starting, setStarting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [playbackUrl, setPlaybackUrl] = useState<string | null>(null)
  const [recordingDuration, setRecordingDuration] = useState(0)
  const [recordingError, setRecordingError] = useState('')
  const [deviceError, setDeviceError] = useState('')
  const [cameraStream, setCameraStream] = useState<MediaStream | null>(null)
  const [micStream, setMicStream] = useState<MediaStream | null>(null)
  const streams = useRef<MediaStream[]>([])
  const generation = useRef(0)
  const recording = useRef<{ recorder: WebcamRecorder; start: Promise<number> } | null>(null)
  const running = started !== null
  const preparing = pending || countdown !== null || waitingForCalibration || starting

  const releaseDevices = useCallback(() => {
    generation.current += 1
    streams.current
      .splice(0)
      .forEach((stream) => stream.getTracks().forEach((track) => track.stop()))
  }, [])

  useEffect(() => {
    return () => {
      const current = recording.current
      recording.current = null
      if (current) {
        void current.start.then(() => current.recorder.stop()).catch(console.error)
      }
      releaseDevices()
    }
  }, [releaseDevices])

  const beginPresenting = useEffectEvent(async () => {
    const request = generation.current
    const tracks = streams.current.flatMap((stream) => stream.getTracks())
    let startedAt: number | undefined
    setStarting(true)
    setCountdown(null)
    setWaitingForCalibration(false)
    if (tracks.length) {
      const recorder = new WebcamRecorder()
      const start = recorder.start(new MediaStream(tracks))
      recording.current = { recorder, start }
      try {
        startedAt = await start
      } catch (error) {
        if (generation.current === request) {
          recording.current = null
          setRecordingError('Could not start the recording.')
          console.error(error)
        }
      }
    }
    if (generation.current !== request) return
    setStarted(startedAt === undefined ? performance.now() : startedAt * 1000)
    setStarting(false)
  })

  // countdown over: start now, or once the camera's calibration has finished
  const finishCountdown = useEffectEvent(() => {
    if (cameraStream && !calibrated) {
      setCountdown(null)
      setWaitingForCalibration(true)
    } else void beginPresenting()
  })

  useEffect(() => {
    if (countdown === null) return
    const timer = window.setTimeout(() => {
      if (countdown > 1) setCountdown(countdown - 1)
      else finishCountdown()
    }, 1000)
    return () => window.clearTimeout(timer)
  }, [countdown])

  // calibration finished after the countdown: start (from a timer, like the countdown itself)
  useEffect(() => {
    if (!waitingForCalibration || !calibrated) return
    const timer = window.setTimeout(() => void beginPresenting(), 0)
    return () => window.clearTimeout(timer)
  }, [waitingForCalibration, calibrated])

  useEffect(() => {
    if (started === null) return
    const timer = window.setInterval(
      () => setSeconds(Math.floor((performance.now() - started) / 1000)),
      250
    )
    return () => window.clearInterval(timer)
  }, [started])

  async function startPractice(): Promise<void> {
    if (preparing || running || saving) return
    const request = ++generation.current
    setSeconds(0)
    setFinished(false)
    setDeviceError('')
    setRecordingError('')
    setPlaybackUrl(null)
    setRecordingDuration(0)
    setPending(true)

    const devices = [
      {
        enabled: camera,
        name: 'Camera',
        // 720p: better pose / hand / face tracking, and fills the 16:9 preview. zoom: lets
        // widestView() zoom a camera that supports it all the way out (more of the room in view)
        constraints: {
          video: {
            width: { ideal: 1280 },
            height: { ideal: 720 },
            zoom: true
          } as MediaTrackConstraints
        },
        setStream: setCameraStream
      },
      {
        enabled: microphone,
        name: 'Microphone',
        // no automatic gain: it turns the mic up during silences, so the room's silence level
        // measured at the start of the session (and every volume reading) would drift
        constraints: { audio: { autoGainControl: false } },
        setStream: setMicStream
      }
    ]
    const errors = await Promise.all(
      devices
        .filter((device) => device.enabled)
        .map(async (device) => {
          try {
            const stream = await navigator.mediaDevices.getUserMedia(device.constraints)
            if (request !== generation.current) {
              stream.getTracks().forEach((track) => track.stop())
              return null
            }
            streams.current.push(stream)
            if (device.name === 'Camera') widestView(stream)
            device.setStream(stream)
            return null
          } catch {
            return device.name
          }
        })
    )
    if (request !== generation.current) return
    setPending(false)
    const unavailable = errors.filter(Boolean)
    if (unavailable.length)
      setDeviceError(`${unavailable.join(' and ')} unavailable. Check your device permissions.`)
    setCountdown(COUNTDOWN_SECONDS)
  }

  /** Waiting for calibration (it waits until the speaker is in frame and still): give up and go
   *  back to the start. Nothing was recorded or scored yet. */
  function cancelPractice(): void {
    if (started !== null) return
    releaseDevices()
    setCameraStream(null)
    setMicStream(null)
    setPending(false)
    setCountdown(null)
    setWaitingForCalibration(false)
    setStarting(false)
  }

  async function endPractice(): ReturnType<WebcamRecorder['stop']> {
    if (started === null) return null
    const duration = (performance.now() - started) / 1000
    setSeconds(Math.floor(duration))
    setRecordingDuration(duration)
    const current = recording.current
    recording.current = null
    const saved = current?.recorder.stop()
    releaseDevices()
    setCameraStream(null)
    setMicStream(null)
    setPending(false)
    setDeviceError('')
    setStarted(null)
    setFinished(true)
    if (!saved) return null
    const request = generation.current
    setSaving(true)
    try {
      const paths = await saved
      if (generation.current === request) setPlaybackUrl(paths?.playbackUrl ?? null)
      return paths
    } catch (error) {
      console.error(error)
      if (generation.current === request) setRecordingError('Could not save the recording.')
      return null
    } finally {
      if (generation.current === request) setSaving(false)
    }
  }

  const logWebcam = useCallback((message: ClientMessage) => {
    recording.current?.recorder.log(message)
  }, [])

  return {
    camera,
    setCamera,
    microphone,
    setMicrophone,
    running,
    startedAt: started === null ? null : started / 1000,
    seconds,
    finished,
    pending,
    preparing,
    countdown,
    waitingForCalibration,
    saving,
    playbackUrl,
    recordingDuration,
    recordingError,
    logWebcam,
    deviceError,
    cameraStream,
    micStream,
    startPractice,
    endPractice,
    cancelPractice
  }
}
