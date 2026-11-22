import { useEffect, useState } from 'react'
import type { PracticeAttempt, PracticeLibrary } from '../../../main/practiceLibrary'
import type { SavedRecording } from '../../../main/webcamRecorder'
import FolderSelect from './FolderSelect'
import PracticeProgress from './PracticeProgress'
import './menu.css'

const recordingDate = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium',
  timeStyle: 'medium'
})

export default function MainMenu({
  practiceBusy,
  onPractice,
  library,
  libraryLoading,
  libraryError,
  onLibraryChange,
  folderId,
  onFolderChange
}: {
  practiceBusy: boolean
  onPractice: () => void
  library: PracticeLibrary
  libraryLoading: boolean
  libraryError: boolean
  onLibraryChange: (library: PracticeLibrary) => void
  folderId: string | null
  onFolderChange: (folderId: string | null) => void
}): React.JSX.Element {
  const [recordings, setRecordings] = useState<SavedRecording[] | null>(null)
  const [listError, setListError] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [playbackError, setPlaybackError] = useState(false)
  const [folderName, setFolderName] = useState('')
  const [changingLibrary, setChangingLibrary] = useState(false)
  const [folderError, setFolderError] = useState('')
  const recordingsById = new Map(recordings?.map((recording) => [recording.id, recording]))
  const savedAttempts = new Map(library.attempts.map((attempt) => [attempt.id, attempt]))
  for (const recording of recordings ?? []) {
    if (!savedAttempts.has(recording.id)) {
      savedAttempts.set(recording.id, {
        id: recording.id,
        completedAt: recording.savedAt,
        score: null,
        folderId: null
      })
    }
  }
  const attempts = [...savedAttempts.values()]
    .filter((attempt) => attempt.folderId === folderId)
    .sort((a, b) => b.completedAt - a.completedAt)
  const selected = attempts.find((attempt) => attempt.id === selectedId)
  const selectedRecording = selected && recordingsById.get(selected.id)

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

  async function createFolder(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    const name = folderName.trim()
    if (!name || changingLibrary) return
    if (
      name.toLowerCase() === 'unfiled' ||
      library.folders.some((folder) => folder.name.toLowerCase() === name.toLowerCase())
    ) {
      setFolderError('A folder with that name already exists.')
      return
    }
    setChangingLibrary(true)
    setFolderError('')
    try {
      const updated = await window.api.practiceLibrary.createFolder(name)
      onLibraryChange(updated)
      const created = updated.folders.find((folder) => folder.name === name)
      if (created) onFolderChange(created.id)
      setFolderName('')
      setSelectedId(null)
    } catch (error) {
      console.error(error)
      setFolderError('Couldn’t create the folder.')
    } finally {
      setChangingLibrary(false)
    }
  }

  async function moveAttempt(attempt: PracticeAttempt, destination: string | null): Promise<void> {
    setChangingLibrary(true)
    setFolderError('')
    try {
      const { id, completedAt } = attempt
      onLibraryChange(await window.api.practiceLibrary.moveAttempt({ id, completedAt }, destination))
      setSelectedId(null)
    } catch (error) {
      console.error(error)
      setFolderError('Couldn’t move this attempt.')
    } finally {
      setChangingLibrary(false)
    }
  }

  return (
    <main className="ph-practice ph-menu">
      <header className="ph-heading">
        <h1>Main Menu</h1>
        <button className="ph-session-action ph-menu-start" type="button" onClick={onPractice}>
          {practiceBusy ? 'Return to Practice' : 'New Practice Session'}
        </button>
      </header>
      <section className="ph-menu-card ph-menu-folders" aria-labelledby="ph-folders-heading">
        <div className="ph-card-head">
          <h2 id="ph-folders-heading">Practice folders</h2>
        </div>
        <div className="ph-menu-folder-controls">
          <FolderSelect
            folders={library.folders}
            label="Current folder"
            value={folderId}
            disabled={changingLibrary}
            onChange={(id) => {
              onFolderChange(id)
              setSelectedId(null)
              setFolderError('')
            }}
          />
          <form onSubmit={(event) => void createFolder(event)}>
            <label className="ph-folder-select">
              New folder name
              <input
                required
                maxLength={80}
                value={folderName}
                onChange={(event) => setFolderName(event.target.value)}
                disabled={changingLibrary || libraryLoading || libraryError}
              />
            </label>
            <button
              type="submit"
              className="ph-script-button"
              disabled={!folderName.trim() || changingLibrary || libraryLoading || libraryError}
            >
              Create folder
            </button>
          </form>
        </div>
        {libraryLoading && <p role="status">Loading practice folders…</p>}
        {libraryError && <p role="status">Couldn’t load practice folders.</p>}
        {folderError && <p role="status">{folderError}</p>}
      </section>
      {!libraryLoading && !libraryError && <PracticeProgress attempts={attempts} />}
      <div className="ph-menu-recordings" data-selected={Boolean(selected)}>
        <section className="ph-menu-card" aria-labelledby="ph-recordings-heading">
          <div className="ph-card-head">
            <h2 id="ph-recordings-heading">Practice attempts</h2>
          </div>
          {listError && <p role="status">Couldn’t load recordings.</p>}
          {!listError && recordings === null && <p role="status">Loading recordings…</p>}
          {!listError && !libraryLoading && recordings && attempts.length === 0 && (
            <p>No saved attempts in this folder yet.</p>
          )}
          {attempts.length > 0 && (
            <ul className="ph-menu-recording-list">
              {attempts.map(({ id, completedAt, score }) => (
                <li key={id}>
                  <button
                    type="button"
                    aria-pressed={selectedId === id}
                    onClick={() => {
                      setPlaybackError(false)
                      setSelectedId(id)
                    }}
                  >
                    <time dateTime={new Date(completedAt).toISOString()}>
                      {recordingDate.format(completedAt)}
                    </time>
                    <span>
                      {score && `${score.points}/${score.maximum} · `}
                      {recordingsById.has(id) ? 'Watch' : 'View'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
        {selected && (
          <section className="ph-menu-card" aria-labelledby="ph-preview-heading">
            <div className="ph-card-head">
              <h2 id="ph-preview-heading">{recordingDate.format(selected.completedAt)}</h2>
            </div>
            {selectedRecording ? (
              <video
                key={selected.id}
                src={selectedRecording.playbackUrl}
                controls
                autoPlay
                playsInline
                onError={() => setPlaybackError(true)}
              />
            ) : (
              <p>No recording is available for this attempt.</p>
            )}
            {playbackError && <p role="status">Couldn’t play this recording.</p>}
            {library.folders.length > 0 && (
              <div className="ph-menu-move">
                <FolderSelect
                  folders={library.folders}
                  label="Move to folder"
                  value={selected.folderId}
                  onChange={(id) => void moveAttempt(selected, id)}
                  disabled={changingLibrary}
                />
              </div>
            )}
          </section>
        )}
      </div>
    </main>
  )
}
