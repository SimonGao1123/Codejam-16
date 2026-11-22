import { useEffect, useRef, useState } from 'react'
import type { VoiceBaseline } from '../practice/audio/audioProtocol'
import VoiceGraph, { type VoiceSample } from '../practice/components/VoiceGraph'
import { CALIBRATION_SECONDS, measureVoiceCalibration } from './calibrationCapture'
import { BLOCK_SECONDS, BLOCK_SIZE, MIN_PITCH_DBFS, pitchHz, volumeDbfs } from './voiceReadings'

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
  // live readings of the sample being recorded, one per 50 ms block (t = seconds into the sample)
  const pitchSamples = useRef<VoiceSample[]>([])
  const volumeSamples = useRef<VoiceSample[]>([])
  const pending = useRef(new Float32Array(0))
  const [hasReadings, setHasReadings] = useState(false)

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
    pitchSamples.current = []
    volumeSamples.current = []
    pending.current = new Float32Array(0)
    setHasReadings(true)
    try {
      const measured = await measureVoiceCalibration(
        controller.signal,
        () => {
          if (!controller.signal.aborted) setStatus('Speak now. Read the line in your usual voice.')
        },
        addReadings
      )
      if (controller.signal.aborted) return
      onSave(measured)
      setStatus('Voice calibration saved.')
    } catch (cause) {
      if (controller.signal.aborted) return
      setStatus('')
      setError(
        cause instanceof DOMException && cause.name === 'NotAllowedError'
          ? 'Allow microphone access to calibrate your voice.'
          : cause instanceof Error
            ? cause.message
            : 'Couldn’t calibrate your voice.'
      )
    } finally {
      if (request.current === controller) {
        request.current = null
        setRecording(false)
      }
    }
  }

  function addReadings(chunk: Float32Array): void {
    const joined = new Float32Array(pending.current.length + chunk.length)
    joined.set(pending.current)
    joined.set(chunk, pending.current.length)
    let i = 0
    for (; i + BLOCK_SIZE <= joined.length; i += BLOCK_SIZE) {
      const block = joined.subarray(i, i + BLOCK_SIZE)
      const t = volumeSamples.current.length * BLOCK_SECONDS
      const volume = volumeDbfs(block)
      volumeSamples.current.push({ t, value: volume })
      pitchSamples.current.push({ t, value: volume >= MIN_PITCH_DBFS ? pitchHz(block) : null })
    }
    pending.current = joined.slice(i)
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
            <div>
              <dt>Pitch</dt>
              <dd>{baseline.pitch_hz} Hz</dd>
            </div>
            <div>
              <dt>Volume</dt>
              <dd>{baseline.volume_dbfs} dBFS</dd>
            </div>
          </dl>
        ) : (
          <p>Calibrate once to enable pitch and volume grading.</p>
        )}
        <p>
          After “Speak now” appears, read this aloud in your usual voice. The sample lasts{' '}
          {CALIBRATION_SECONDS} seconds.
        </p>
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
        {error && (
          <p className="ph-error" role="alert">
            {error}
          </p>
        )}
      </section>
      {hasReadings && (
        <div className="ph-voice-calibration-graphs">
          <section className="ph-pitch" aria-labelledby="ph-cal-pitch">
            <div className="ph-card-head">
              <h2 id="ph-cal-pitch">Pitch</h2>
              <span>
                {recording ? 'Recording…' : baseline ? `Reference ${baseline.pitch_hz} Hz` : ''}
              </span>
            </div>
            <VoiceGraph
              metric="pitch"
              samples={pitchSamples}
              typicalHz={recording ? null : (baseline?.pitch_hz ?? null)}
              durationS={CALIBRATION_SECONDS}
              live={recording}
            />
          </section>
          <section className="ph-pitch" aria-labelledby="ph-cal-volume">
            <div className="ph-card-head">
              <h2 id="ph-cal-volume">Volume</h2>
              <span>
                {recording
                  ? 'Recording…'
                  : baseline
                    ? `Reference ${baseline.volume_dbfs} dBFS`
                    : ''}
              </span>
            </div>
            <VoiceGraph
              metric="volume"
              samples={volumeSamples}
              typicalDbfs={recording ? null : (baseline?.volume_dbfs ?? null)}
              durationS={CALIBRATION_SECONDS}
              live={recording}
            />
          </section>
        </div>
      )}
    </main>
  )
}
