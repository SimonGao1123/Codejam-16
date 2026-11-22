// Calibration and analysis status beneath the camera preview. Calibration isn't manual: every
// practice session starts by recording the resting position once the speaker is in frame and
// still (3 s), while the get-ready countdown also measures the room's silence for the voice side.

import { useEffect, useRef } from 'react'

import type { WebcamHandle, WebcamStatus } from './WebcamView'

type Props = {
  webcam: React.RefObject<WebcamHandle | null>
  status: WebcamStatus
  cameraStream: MediaStream | null // a new stream = a new practice session
  onCalibrated: (done: boolean) => void // this session's calibration has finished
}

// Record 3 s once the speaker is in frame and still. The service decides whether hands are required.
const SESSION_CALIBRATION = { delay: 0, duration: 3, when_ready: true }

function calibrationText(cal: WebcamStatus['calibration'], sessionOn: boolean): string {
  if (!sessionOn) return 'Calibrates when you start'
  if (cal?.phase === 'arming') return cal.message // e.g. "Waiting to calibrate: show both hands"
  if (cal?.phase === 'recording' || cal?.phase === 'waiting')
    return `Calibrating: stand still · ${cal.remaining.toFixed(1)}s`
  if (cal?.calibrated) return 'Calibrated'
  return cal ? 'Not calibrated: face and shoulders not in frame' : 'Calibrating soon…'
}

export default function WebcamControls({
  webcam,
  status,
  cameraStream,
  onCalibrated
}: Props): React.JSX.Element {
  const cal = status.calibration
  const busy = cal?.phase === 'arming' || cal?.phase === 'waiting' || cal?.phase === 'recording'

  // start of each session: calibrate as soon as frames are being analysed and Python is connected
  const calibratedStream = useRef<MediaStream | null>(null)
  const sawCalibrating = useRef(false)
  useEffect(() => {
    if (!cameraStream || !status.ready || !status.connected) return
    if (calibratedStream.current === cameraStream) return
    calibratedStream.current = cameraStream
    sawCalibrating.current = false
    webcam.current?.send({ type: 'calibrate', t: performance.now() / 1000, ...SESSION_CALIBRATION })
  }, [cameraStream, status.ready, status.connected, webcam])

  // done = this session's calibration was seen running and has since finished (not a leftover
  // "calibrated" state from the previous session)
  const calibrated = Boolean(cal?.calibrated)
  useEffect(() => {
    if (!cameraStream || calibratedStream.current !== cameraStream) return onCalibrated(false)
    if (busy) sawCalibrating.current = true
    onCalibrated(sawCalibrating.current && !busy && calibrated)
  }, [cameraStream, busy, calibrated, onCalibrated])

  return (
    <div className="ph-webcam-bar">
      <span
        className="ph-calibration"
        data-state={busy ? 'busy' : cal?.calibrated ? 'done' : 'off'}
        role="status"
      >
        {calibrationText(cal, Boolean(cameraStream))}
      </span>
      <span className="ph-webcam-status" data-connected={status.connected}>
        {status.connected ? 'Webcam analysis connected' : 'Webcam analysis not connected'}
      </span>
      {status.error && <span className="ph-error">{status.error}</span>}
    </div>
  )
}
