import { useEffect, useState } from 'react'
import type { SavedRecording } from '../../../main/webcamRecorder'
import './menu.css'

const recordingDate = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium',
  timeStyle: 'medium'
})

export default function MainMenu({
  practiceBusy,
  onPractice
}: {
  practiceBusy: boolean
  onPractice: () => void
}): React.JSX.Element {
  const [recordings, setRecordings] = useState<SavedRecording[] | null>(null)
  const [listError, setListError] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [playbackError, setPlaybackError] = useState(false)
  const selected = recordings?.find((recording) => recording.id === selectedId)

  useEffect(() => {
    let cancelled = false
    window.api.webcamRecorder.list().then(
      (entries) => {
        if (cancelled) return
        setRecordings(entries)
        setListError(false)
      },
      () => {
        if (!cancelled) setListError(true)
      }
    )
    return () => {
      cancelled = true
    }
  }, [practiceBusy])

  return (
    <main className="ph-practice ph-menu">
      <header className="ph-heading">
        <h1>Main Menu</h1>
        <button className="ph-session-action ph-menu-start" type="button" onClick={onPractice}>
          {practiceBusy ? 'Return to Practice' : 'New Practice Session'}
        </button>
      </header>
      <div className="ph-menu-recordings" data-selected={Boolean(selected)}>
        <section className="ph-menu-card" aria-labelledby="ph-recordings-heading">
          <div className="ph-card-head">
            <h2 id="ph-recordings-heading">Saved recordings</h2>
          </div>
          {listError && <p role="status">Couldn’t load recordings.</p>}
          {!listError && recordings === null && <p role="status">Loading recordings…</p>}
          {!listError && recordings?.length === 0 && <p>No saved recordings yet.</p>}
          {!!recordings?.length && (
            <ul className="ph-menu-recording-list">
              {recordings.map((recording) => (
                <li key={recording.id}>
                  <button
                    type="button"
                    aria-pressed={selectedId === recording.id}
                    onClick={() => {
                      setPlaybackError(false)
                      setSelectedId(recording.id)
                    }}
                  >
                    <time dateTime={new Date(recording.savedAt).toISOString()}>
                      {recordingDate.format(recording.savedAt)}
                    </time>
                    <span>Watch</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
        {selected && (
          <section className="ph-menu-card" aria-labelledby="ph-preview-heading">
            <div className="ph-card-head">
              <h2 id="ph-preview-heading">{recordingDate.format(selected.savedAt)}</h2>
            </div>
            <video
              key={selected.id}
              src={selected.playbackUrl}
              controls
              autoPlay
              playsInline
              onError={() => setPlaybackError(true)}
            />
            {playbackError && <p role="status">Couldn’t play this recording.</p>}
          </section>
        )}
      </div>
    </main>
  )
}
