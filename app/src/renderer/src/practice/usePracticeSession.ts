import { useCallback, useEffect, useRef, useState } from 'react'

export function usePracticeSession() {
  const [camera, setCamera] = useState(true)
  const [microphone, setMicrophone] = useState(true)
  const [started, setStarted] = useState<number | null>(null)
  const [seconds, setSeconds] = useState(0)
  const [finished, setFinished] = useState(false)
  const [pending, setPending] = useState(false)
  const [deviceError, setDeviceError] = useState('')
  const [cameraStream, setCameraStream] = useState<MediaStream | null>(null)
  const [micStream, setMicStream] = useState<MediaStream | null>(null)
  const streams = useRef<MediaStream[]>([])
  const generation = useRef(0)
  const running = started !== null

  const releaseDevices = useCallback(() => {
    generation.current += 1
    streams.current
      .splice(0)
      .forEach((stream) => stream.getTracks().forEach((track) => track.stop()))
  }, [])

  useEffect(() => releaseDevices, [releaseDevices])

  useEffect(() => {
    if (started === null) return
    const timer = window.setInterval(
      () => setSeconds(Math.floor((performance.now() - started) / 1000)),
      250
    )
    return () => window.clearInterval(timer)
  }, [started])

  async function startPractice(): Promise<void> {
    const request = ++generation.current
    setStarted(performance.now())
    setSeconds(0)
    setFinished(false)
    setDeviceError('')
    setPending(camera || microphone)

    const devices = [
      { enabled: camera, name: 'Camera', constraints: { video: true }, setStream: setCameraStream },
      {
        enabled: microphone,
        name: 'Microphone',
        constraints: { audio: true },
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
  }

  function endPractice(): void {
    if (started !== null) setSeconds(Math.floor((performance.now() - started) / 1000))
    releaseDevices()
    setCameraStream(null)
    setMicStream(null)
    setPending(false)
    setDeviceError('')
    setStarted(null)
    setFinished(true)
  }

  return {
    camera,
    setCamera,
    microphone,
    setMicrophone,
    running,
    seconds,
    finished,
    pending,
    deviceError,
    cameraStream,
    micStream,
    startPractice,
    endPractice
  }
}
