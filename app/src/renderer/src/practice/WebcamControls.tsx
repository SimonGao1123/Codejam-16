// Calibrate / Record / analysis status bar under the camera preview. Recording saves the session's
// camera (+ microphone) as .webm and every frame sent to Python as .jsonl, and stops by itself
// when the practice session ends.

import { useEffect, useEffectEvent, useRef, useState } from 'react'

import type { WebcamHandle, WebcamStatus } from './WebcamView'
import { WebcamRecorder } from './webcamRecorder'

type Props = {
  webcam: React.RefObject<WebcamHandle | null>
  status: WebcamStatus
  cameraStream: MediaStream | null
  micStream: MediaStream | null
}

function calibrateLabel(cal: WebcamStatus['calibration']): string {
  if (cal?.phase === 'waiting') return `Get ready... ${cal.remaining.toFixed(1)}s`
  if (cal?.phase === 'recording') return `Hold still... ${cal.remaining.toFixed(1)}s`
  return cal?.calibrated ? 'Recalibrate (c)' : 'Calibrate (c)'
}

export default function WebcamControls({
  webcam,
  status,
  cameraStream,
  micStream
}: Props): React.JSX.Element {
  const recorder = useRef(new WebcamRecorder())
  const [recording, setRecording] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)

  const busy = status.calibration?.phase === 'waiting' || status.calibration?.phase === 'recording'
  const canCalibrate = status.ready && status.connected && !busy

  function calibrate(): void {
    if (canCalibrate) webcam.current?.send({ type: 'calibrate', t: performance.now() / 1000 })
  }

  async function stopRecording(): Promise<void> {
    webcam.current?.setLogger(null)
    const paths = await recorder.current.stop()
    setRecording(false)
    if (paths) setSaved(`Saved ${paths.video} and ${paths.data}`)
  }

  async function toggleRecording(): Promise<void> {
    const rec = recorder.current
    if (rec.active) return stopRecording()
    const cam = webcam.current
    if (!cameraStream || !cam) return
    const tracks = [...cameraStream.getVideoTracks(), ...(micStream?.getAudioTracks() ?? [])]
    await rec.start(new MediaStream(tracks), performance.now() / 1000)
    cam.setLogger((msg) => rec.log(msg))
    setRecording(true)
    setSaved(null)
  }

  // practice session ended (camera stream gone): finish the recording
  const onCameraGone = useEffectEvent(() => {
    if (recorder.current.active) void stopRecording()
  })
  useEffect(() => {
    if (!cameraStream) onCameraGone()
  }, [cameraStream])

  // leaving the page while recording: still finish writing the files
  useEffect(() => {
    const rec = recorder.current
    return () => {
      if (rec.active) void rec.stop()
    }
  }, [])

  // c = calibrate (ignored while typing in a text field)
  const onKey = useEffectEvent((e: KeyboardEvent) => {
    const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement
    if (e.key === 'c' && !e.metaKey && !e.ctrlKey && !typing) calibrate()
  })
  useEffect(() => {
    const listener = (e: KeyboardEvent): void => onKey(e)
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [])

  return (
    <div className="ph-webcam-bar">
      <button onClick={calibrate} disabled={!canCalibrate} data-active={busy}>
        {calibrateLabel(status.calibration)}
      </button>
      <button
        onClick={() => void toggleRecording()}
        disabled={!status.ready && !recording}
        data-recording={recording}
      >
        {recording ? '■ Stop recording' : '● Record'}
      </button>
      <span className="ph-webcam-status" data-connected={status.connected}>
        {status.connected ? 'Webcam analysis connected' : 'Webcam analysis not connected'}
      </span>
      {status.error && <span className="ph-error">{status.error}</span>}
      {saved && <span className="ph-webcam-note">{saved}</span>}
    </div>
  )
}
