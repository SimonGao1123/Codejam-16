import { useEffect, useRef, useState } from 'react'
import type { VoiceBaseline } from '../practice/audio/audioProtocol'
import { CALIBRATION_SECONDS, measureVoiceCalibration } from './calibrationCapture'

export default function VoiceCalibration({
  baseline,
  onSave
}: {
  baseline: VoiceBaseline | null
  onSave: (baseline: VoiceBaseline) => void
}): React.JSX.Element {
  const request = useRef<AbortController | null>(null)
  const [recording, setRecording] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  useEffect(() => () => request.current?.abort(), [])

  function cancel(): void {
    request.current?.abort()
    request.current = null
    setRecording(false)
    setStatus('')
  }

  async function calibrate(): Promise<void> {
    if (request.current) return
    const controller = new AbortController()
    request.current = controller
    setRecording(true)
    setStatus('Opening microphone…')
    setError('')
    try {
      const measured = await measureVoiceCalibration(controller.signal, () => {
        if (!controller.signal.aborted) setStatus('Speak now. Read the line in your usual voice.')
      })
      if (controller.signal.aborted) return
      onSave(measured)
      setStatus('Voice calibration saved.')
    } catch (cause) {
      if (controller.signal.aborted) return
      setStatus('')
      setError(cause instanceof DOMException && cause.name === 'NotAllowedError'
        ? 'Allow microphone access to calibrate your voice.'
        : cause instanceof Error ? cause.message : 'Couldn’t calibrate your voice.')
    } finally {
      if (request.current === controller) {
        request.current = null
        setRecording(false)
      }
    }
  }

  return (
    <main className="ph-practice">
      <header className="ph-heading">
        <div>
          <h1>Voice calibration</h1>
          <p>Set the pitch and volume reference used for practice alerts and scores.</p>
        </div>
      </header>
      <section className="ph-results" aria-labelledby="ph-voice-reference">
        <div className="ph-card-head">
          <h2 id="ph-voice-reference">Saved voice reference</h2>
        </div>
        {baseline ? (
          <dl className="ph-results-breakdown">
            <div><dt>Pitch</dt><dd>{baseline.pitch_hz} Hz</dd></div>
            <div><dt>Volume</dt><dd>{baseline.volume_dbfs} dBFS</dd></div>
          </dl>
        ) : (
          <p>Calibrate once to enable pitch and volume grading.</p>
        )}
        <p>After “Speak now” appears, read this aloud in your usual voice. The sample lasts {CALIBRATION_SECONDS} seconds.</p>
        <p>“Today I’ll share what we learned and explain what we plan to do next.”</p>
        <div className="ph-voice-calibration-actions">
          {recording ? (
            <button className="ph-script-button" type="button" onClick={cancel}>
              Cancel
            </button>
          ) : (
            <button className="ph-script-button" type="button" onClick={() => void calibrate()}>
              {baseline ? 'Recalibrate voice' : 'Calibrate voice'}
            </button>
          )}
        </div>
        {status && <p role="status">{status}</p>}
        {error && <p className="ph-error" role="alert">{error}</p>}
      </section>
    </main>
  )
}
