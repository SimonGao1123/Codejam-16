import { useCallback, useEffect, useRef, useState } from 'react'

import { WebcamView, type WebcamHandle, type WebcamStatus } from './components/WebcamView'
import { WebcamRecorder } from './webcam/recorder'

function calibrateLabel(cal: WebcamStatus['calibration']): string {
  if (cal?.phase === 'waiting') return `Get ready... ${cal.remaining.toFixed(1)}s`
  if (cal?.phase === 'recording') return `Hold still... ${cal.remaining.toFixed(1)}s`
  return cal?.calibrated ? 'Recalibrate (c)' : 'Calibrate (c)'
}

function App(): React.JSX.Element {
  const webcam = useRef<WebcamHandle>(null)
  const recorder = useRef(new WebcamRecorder())
  const [status, setStatus] = useState<WebcamStatus>({
    ready: false,
    error: null,
    connected: false,
    calibration: null
  })
  const [recording, setRecording] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)

  const busy = status.calibration?.phase === 'waiting' || status.calibration?.phase === 'recording'
  const canCalibrate = status.ready && status.connected && !busy

  const calibrate = useCallback(() => {
    if (canCalibrate) webcam.current?.send({ type: 'calibrate', t: performance.now() / 1000 })
  }, [canCalibrate])

  const toggleRecording = useCallback(async () => {
    const rec = recorder.current
    const cam = webcam.current
    if (rec.active) {
      cam?.setLogger(null)
      const paths = await rec.stop()
      setRecording(false)
      if (paths) setSaved(`Saved ${paths.video} and ${paths.data}`)
    } else {
      const stream = cam?.stream()
      if (!cam || !stream) return
      await rec.start(stream, performance.now() / 1000)
      cam.setLogger((msg) => rec.log(msg))
      setRecording(true)
      setSaved(null)
    }
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'c' && !e.metaKey && !e.ctrlKey) calibrate()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [calibrate])

  return (
    <div className="app">
      <WebcamView ref={webcam} onStatus={setStatus} />
      <div className="toolbar">
        <button onClick={calibrate} disabled={!canCalibrate} className={busy ? 'active' : ''}>
          {calibrateLabel(status.calibration)}
        </button>
        <button
          onClick={toggleRecording}
          disabled={!status.ready}
          className={recording ? 'recording' : ''}
        >
          {recording ? '■ Stop recording' : '● Record'}
        </button>
        <span className={`pill ${status.connected ? 'ok' : 'off'}`}>
          {status.connected ? 'Webcam analysis connected' : 'Webcam analysis not connected'}
        </span>
        {!status.ready && !status.error && <span className="note">Loading camera and models…</span>}
        {status.error && <span className="note error">{status.error}</span>}
        {saved && <span className="note">{saved}</span>}
      </div>
    </div>
  )
}

export default App
