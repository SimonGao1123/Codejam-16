// Optional script for the practice session. Before practising: paste it or attach a PDF (its text
// is read into the box for review). While practising: the script split into parts, with the
// current part highlighted and skipped parts struck through. No script = script tracking is off.
// Underneath: the time the user has, which with a script sets the desired pace (words / time).

import { useEffect, useRef, useState } from 'react'

import { useAlertPreferences } from '../../liveAlert/useAlertPreferences'
import type { AudioAnalysis } from '../audio/useAudioAnalysis'
import { pdfText } from '../script/pdfText'

type Props = {
  script: string
  setScript: (text: string) => void
  running: boolean
  speech: AudioAnalysis
  targetMinutes: number | null // the time the user has to present; null = not set
  setTargetMinutes: (minutes: number | null) => void
}

export default function ScriptPanel({
  script,
  setScript,
  running,
  speech,
  targetMinutes,
  setTargetMinutes
}: Props): React.JSX.Element {
  const { scriptChunks: chunks, scriptReady, scriptPosition: pos, connected } = speech
  const [editing, setEditing] = useState(true)
  const [pdfStatus, setPdfStatus] = useState<{ text: string; error: boolean } | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLOListElement>(null)
  const hasScript = chunks.length > 0

  // starting a session switches to the parts view (state adjusted during render, React's pattern
  // for reacting to a prop change without an extra effect pass)
  const [wasRunning, setWasRunning] = useState(running)
  if (running !== wasRunning) {
    setWasRunning(running)
    if (running) setEditing(false)
  }

  // keep the current part in view
  useEffect(() => {
    const item = listRef.current?.children[pos?.position ?? -1] as HTMLElement | undefined
    item?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [pos?.position])

  async function attachPdf(file: File | undefined): Promise<void> {
    if (!file) return
    setPdfStatus({ text: `Reading ${file.name}…`, error: false })
    try {
      const text = await pdfText(file)
      setScript(text)
      setPdfStatus({ text: `Read from ${file.name}: check the text above`, error: false })
    } catch (err) {
      setPdfStatus({ text: err instanceof Error ? err.message : String(err), error: true })
    } finally {
      if (fileRef.current) fileRef.current.value = '' // allow attaching the same file again
    }
  }

  // desired pace: Python's script words / time when both are set, else the Live alerts Target pace
  const [preferences] = useAlertPreferences()
  const sliderPace = preferences.rules.pace?.threshold ?? 140
  const target = speech.paceTarget
  const paceText =
    target?.source === 'script' && script.trim() && targetMinutes
      ? `Desired pace: ${target.desired_wpm.toFixed(0)} WPM (${target.script_words.toLocaleString()} words in ${targetMinutes} min)`
      : targetMinutes
        ? `Add a script to set your pace from your time. Until then: ${sliderPace} WPM (Live alerts target pace)`
        : `Desired pace: ${sliderPace} WPM (Live alerts target pace). Enter your time and a script to pace yourself to finish on time.`

  const showParts = !editing && hasScript
  const status = pos
    ? pos.position >= 0
      ? `Part ${pos.position + 1} of ${chunks.length} · ${pos.status}`
      : `Not started · ${pos.status}`
    : running
      ? 'Listening for your script…'
      : `${chunks.length} parts`

  return (
    <section className="ph-script" aria-labelledby="ph-script-heading">
      <div className="ph-card-head">
        <h2 id="ph-script-heading">Script</h2>
        <span>
          {!script.trim()
            ? 'Optional · script tracking is off'
            : !connected
              ? 'Speech analysis not connected'
              : !hasScript
                ? 'Splitting into parts…'
                : !scriptReady
                  ? `${chunks.length} parts · loading the comparison model…`
                  : showParts
                    ? status
                    : `${chunks.length} parts · tracked while you practice`}
        </span>
      </div>

      {showParts ? (
        <>
          <ol className="ph-script-parts" ref={listRef}>
            {chunks.map((chunk, i) => (
              <li
                key={i}
                data-state={
                  i === pos?.position
                    ? 'current'
                    : pos?.skipped.includes(i)
                      ? 'skipped'
                      : pos && i < pos.position
                        ? 'done'
                        : undefined
                }
                data-best={pos && i === pos.best && i !== pos.position ? true : undefined}
              >
                {chunk}
              </li>
            ))}
          </ol>
          <div className="ph-script-bar">
            {pos && pos.skipped.length > 0 && (
              <span className="ph-error">
                Skipped: part{pos.skipped.length > 1 ? 's' : ''}{' '}
                {pos.skipped.map((i) => i + 1).join(', ')}
              </span>
            )}
            {!running && (
              <button className="ph-script-button" onClick={() => setEditing(true)}>
                Edit script
              </button>
            )}
          </div>
        </>
      ) : (
        <div className="ph-script-edit">
          <textarea
            value={script}
            onChange={(e) => setScript(e.target.value)}
            disabled={running}
            placeholder="Paste your script here, or attach a PDF. Leave it empty to practise without script tracking."
            aria-label="Your script"
          />
          <div className="ph-script-bar">
            <button
              className="ph-script-button"
              onClick={() => fileRef.current?.click()}
              disabled={running}
            >
              Attach PDF
            </button>
            <input
              ref={fileRef}
              type="file"
              accept="application/pdf,.pdf"
              hidden
              onChange={(e) => void attachPdf(e.target.files?.[0])}
            />
            {script && (
              <button
                className="ph-script-button"
                onClick={() => {
                  setScript('')
                  setPdfStatus(null)
                }}
                disabled={running}
              >
                Clear
              </button>
            )}
            {pdfStatus && (
              <span className={pdfStatus.error ? 'ph-error' : 'ph-webcam-note'}>
                {pdfStatus.text}
              </span>
            )}
          </div>
        </div>
      )}
      <div className="ph-script-pace">
        <label>
          Time you have
          <input
            type="number"
            min={0.5}
            step={0.5}
            inputMode="decimal"
            value={targetMinutes ?? ''}
            onChange={(e) => {
              const minutes = parseFloat(e.target.value)
              setTargetMinutes(minutes > 0 ? minutes : null)
            }}
            disabled={running}
            placeholder="–"
            aria-label="Time you have, in minutes"
          />
          min
        </label>
        <span>{paceText}</span>
      </div>
    </section>
  )
}
