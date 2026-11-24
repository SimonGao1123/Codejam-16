// Optional script for the practice session. Before practising: paste it or attach a PDF (its text
// is read into the box for review). While practising: the script split into parts, with the
// current part highlighted and skipped parts struck through. No script = script tracking is off.
// Underneath: the time the user has, which with a script sets the desired pace (words / time).

import { useEffect, useRef, useState } from 'react'

import type { AudioAnalysis } from '../audio/useAudioAnalysis'
import ScriptEditor from './ScriptEditor'
import ScriptPace from './ScriptPace'

type Props = {
  script: string
  setScript: (text: string) => void
  running: boolean
  speech: AudioAnalysis
  targetSeconds: number | null // the time the user has to present; null = not set
  setTargetSeconds: (seconds: number | null) => void
}

function formatDuration(total: number): string {
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return [minutes && `${minutes} min`, seconds && `${seconds} sec`].filter(Boolean).join(' ')
}

export default function ScriptPanel({
  script,
  setScript,
  running,
  speech,
  targetSeconds,
  setTargetSeconds
}: Props): React.JSX.Element {
  const { scriptChunks: chunks, scriptReady, scriptPosition: pos, connected } = speech
  const [editing, setEditing] = useState(true)
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

  // desired pace: Python's script words / time when both are set, else the Settings target pace
  const target = speech.paceTarget
  const scriptPace =
    target?.source === 'script' && script.trim() && targetSeconds
      ? `Desired pace: ${target.desired_wpm.toFixed(0)} WPM (${target.script_words.toLocaleString()} words in ${formatDuration(targetSeconds)})`
      : null

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
        <ScriptEditor
          script={script}
          setScript={setScript}
          disabled={running}
          placeholder="Paste your script here, or attach a PDF. Leave it empty to practise without script tracking."
        />
      )}
      <ScriptPace
        targetSeconds={targetSeconds}
        setTargetSeconds={setTargetSeconds}
        disabled={running}
        scriptPace={scriptPace}
      />
    </section>
  )
}
