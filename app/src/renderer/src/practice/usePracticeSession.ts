import { useCallback, useEffect, useEffectEvent, useRef, useState } from 'react'
import { WebcamRecorder } from './camera/webcamRecorder'
import type { ClientMessage } from './camera/webcamProtocol'

const COUNTDOWN_SECONDS = 3

export function usePracticeSession() {
  const [camera, setCamera] = useState(true)
  const [microphone, setMicrophone] = useState(true)
  const [started, setStarted] = useState<number | null>(null)
  const [seconds, setSeconds] = useState(0)
  const [finished, setFinished] = useState(false)
  const [discarded, setDiscarded] = useState(false) // stopped before calibration finished
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
  const preparing = pending || countdown !== null || starting

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

  useEffect(() => {
    if (countdown === null) return
    const timer = window.setTimeout(() => {
      if (countdown > 1) setCountdown(countdown - 1)
      else void beginPresenting()
    }, 1000)
    return () => window.clearTimeout(timer)
  }, [countdown])

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
    setDiscarded(false)
    setDeviceError('')
    setRecordingError('')
    setPlaybackUrl(null)
    setRecordingDuration(0)
    setPending(true)

    const devices = [
      {
        enabled: camera,
        name: 'Camera',
        // 720p: better pose / hand / face tracking, and fills the 16:9 preview
        constraints: { video: { width: { ideal: 1280 }, height: { ideal: 720 } } },
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

  /** discard: stopped before calibration finished, so nothing is scored and the recording is
   *  deleted instead of saved. */
  function endPractice(discard = false): void {
    if (started === null) return
    const duration = (performance.now() - started) / 1000
    setSeconds(Math.floor(duration))
    setRecordingDuration(duration)
    const current = recording.current
    recording.current = null
    const saved = current?.recorder.stop(discard)
    releaseDevices()
    setCameraStream(null)
    setMicStream(null)
    setPending(false)
    setDeviceError('')
    setStarted(null)
    setFinished(true)
    setDiscarded(discard)
    if (saved && !discard) {
      const request = generation.current
      setSaving(true)
      void saved
        .then((paths) => {
          if (generation.current === request) setPlaybackUrl(paths?.playbackUrl ?? null)
        })
        .catch((error) => {
          console.error(error)
          if (generation.current === request) setRecordingError('Could not save the recording.')
        })
        .finally(() => {
          if (generation.current === request) setSaving(false)
        })
    } else if (saved) {
      void saved.catch(console.error) // discarded: deleting the files
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
    discarded,
    pending,
    preparing,
    countdown,
    saving,
    playbackUrl,
    recordingDuration,
    recordingError,
    logWebcam,
    deviceError,
    cameraStream,
    micStream,
    startPractice,
    endPractice
  }
}
