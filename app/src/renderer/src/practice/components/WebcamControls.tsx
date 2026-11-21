// Calibration and analysis status beneath the camera preview.

import { useEffect, useEffectEvent } from 'react'

import type { WebcamHandle, WebcamStatus } from './WebcamView'

type Props = {
  webcam: React.RefObject<WebcamHandle | null>
  status: WebcamStatus
}

function calibrateLabel(cal: WebcamStatus['calibration']): string {
  if (cal?.phase === 'waiting') return `Get ready... ${cal.remaining.toFixed(1)}s`
  if (cal?.phase === 'recording') return `Hold still... ${cal.remaining.toFixed(1)}s`
  return cal?.calibrated ? 'Recalibrate (c)' : 'Calibrate (c)'
}

export default function WebcamControls({
  webcam,
  status
}: Props): React.JSX.Element {
  const busy = status.calibration?.phase === 'waiting' || status.calibration?.phase === 'recording'
  const canCalibrate = status.ready && status.connected && !busy

  function calibrate(): void {
    if (canCalibrate) webcam.current?.send({ type: 'calibrate', t: performance.now() / 1000 })
  }

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
      <span className="ph-webcam-status" data-connected={status.connected}>
        {status.connected ? 'Webcam analysis connected' : 'Webcam analysis not connected'}
      </span>
      {status.error && <span className="ph-error">{status.error}</span>}
    </div>
  )
}
