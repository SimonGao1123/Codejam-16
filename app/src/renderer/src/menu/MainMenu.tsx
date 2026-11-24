import { useEffect, useRef, useState } from 'react'
import type { PracticeAttempt, PracticeLibrary } from '../../../main/practiceLibrary'
import type { SavedRecording } from '../../../main/webcamRecorder'
import { max1, score1 } from '../practice/scoreFormat'
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
  const [deletedIds, setDeletedIds] = useState<string[]>([])
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<{ id: string; text: string } | null>(null)
  const [folderName, setFolderName] = useState('')
  const [changingLibrary, setChangingLibrary] = useState(false)
  const [folderError, setFolderError] = useState('')
  const [createError, setCreateError] = useState('') // shown in the name dialog
  // one dialog for typing a name: a new folder, renaming the current folder, renaming an attempt
  const [nameMode, setNameMode] = useState<'create' | 'renameFolder' | 'renameAttempt'>('create')
  const newFolderDialog = useRef<HTMLDialogElement>(null)
  const deleteFolderDialog = useRef<HTMLDialogElement>(null)
  const currentFolder = library.folders.find((folder) => folder.id === folderId) ?? null
  const currentFolderAttempts = library.attempts.filter((attempt) => attempt.folderId === folderId)
  const recordingsById = new Map(
    recordings
      ?.filter(({ id }) => !deletedIds.includes(id))
      .map((recording) => [recording.id, recording])
  )
  const savedAttempts = new Map(library.attempts.map((attempt) => [attempt.id, attempt]))
  for (const recording of recordingsById.values()) {
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

  function openNameDialog(mode: typeof nameMode, initial = ''): void {
    setNameMode(mode)
    setFolderName(initial)
    setCreateError('')
    newFolderDialog.current?.showModal()
  }

  async function submitName(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    const name = folderName.trim()
    if (changingLibrary || (!name && nameMode !== 'renameAttempt')) return
    const renaming = nameMode === 'renameFolder' ? folderId : null
    if (
      nameMode !== 'renameAttempt' &&
      (name.toLowerCase() === 'unfiled' ||
        library.folders.some(
          (folder) => folder.id !== renaming && folder.name.toLowerCase() === name.toLowerCase()
        ))
    ) {
      setCreateError('A folder with that name already exists.')
      return
    }
    setChangingLibrary(true)
    setCreateError('')
    try {
      const api = window.api.practiceLibrary
      if (nameMode === 'create') {
        const updated = await api.createFolder(name)
        onLibraryChange(updated)
        const created = updated.folders.find((folder) => folder.name === name)
        if (created) onFolderChange(created.id)
        setSelectedId(null)
      } else if (nameMode === 'renameFolder' && folderId) {
        onLibraryChange(await api.renameFolder(folderId, name))
      } else if (nameMode === 'renameAttempt' && selected) {
        const { id, completedAt } = selected
        onLibraryChange(await api.renameAttempt({ id, completedAt }, name))
      }
      setFolderName('')
      newFolderDialog.current?.close()
    } catch (error) {
      console.error(error)
      setCreateError(
        nameMode === 'create' ? 'Couldn’t create the folder.' : 'Couldn’t save the new name.'
      )
    } finally {
      setChangingLibrary(false)
    }
  }

  async function deleteFolder(deleteAttempts: boolean): Promise<void> {
    if (!folderId || changingLibrary) return
    setChangingLibrary(true)
    setFolderError('')
    try {
      const removed = deleteAttempts ? currentFolderAttempts.map(({ id }) => id) : []
      onLibraryChange(await window.api.practiceLibrary.deleteFolder(folderId, deleteAttempts))
      if (removed.length) setDeletedIds((ids) => [...ids, ...removed])
      onFolderChange(null)
      setSelectedId(null)
      deleteFolderDialog.current?.close()
    } catch (error) {
      console.error(error)
      setFolderError('Couldn’t delete the folder.')
      deleteFolderDialog.current?.close()
    } finally {
      setChangingLibrary(false)
    }
  }

  async function moveAttempt(attempt: PracticeAttempt, destination: string | null): Promise<void> {
    setChangingLibrary(true)
    setFolderError('')
    try {
      const { id, completedAt } = attempt
      onLibraryChange(
        await window.api.practiceLibrary.moveAttempt({ id, completedAt }, destination)
      )
      setSelectedId(null)
    } catch (error) {
      console.error(error)
      setFolderError('Couldn’t move this attempt.')
    } finally {
      setChangingLibrary(false)
    }
  }

  /** Deletes an attempt's video only (the score stays) or the whole session. */
  async function deleteSaved(attempt: PracticeAttempt, videoOnly: boolean): Promise<void> {
    if (practiceBusy || changingLibrary) return
    const date = recordingDate.format(attempt.completedAt)
    if (
      !window.confirm(
        videoOnly
          ? `Permanently delete the video from ${date}? Its score stays.`
          : `Permanently delete the practice session from ${date}, including its recording and score?`
      )
    ) {
      return
    }

    setChangingLibrary(true)
    setDeletingId(attempt.id)
    setDeleteError(null)
    try {
      const api = window.api.practiceLibrary
      onLibraryChange(
        videoOnly ? await api.deleteRecording(attempt.id) : await api.deleteAttempt(attempt.id)
      )
      setPlaybackError(false)
      setDeletedIds((ids) => [...ids, attempt.id])
      if (!videoOnly) setSelectedId((id) => (id === attempt.id ? null : id))
    } catch (error) {
      console.error(error)
      setDeleteError({
        id: attempt.id,
        text: `Couldn’t delete this ${videoOnly ? 'video' : 'session'}. Try again.`
      })
    } finally {
      setDeletingId(null)
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
        {!libraryLoading && !libraryError && library.folders.length === 0 ? (
          <div className="ph-menu-empty">
            <p className="ph-menu-empty-title">No folders found</p>
            <p>
              Folders group practice runs of the same presentation. Attempts without a folder are
              listed under Practice attempts below.
            </p>
            <button
              className="ph-script-button"
              type="button"
              disabled={changingLibrary}
              onClick={() => openNameDialog('create')}
            >
              New folder
            </button>
          </div>
        ) : (
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
            {currentFolder && (
              <div className="ph-menu-folder-actions">
                <button
                  className="ph-menu-secondary"
                  type="button"
                  disabled={changingLibrary}
                  onClick={() => openNameDialog('renameFolder', currentFolder.name)}
                >
                  Rename folder
                </button>
                <button
                  className="ph-menu-secondary ph-menu-danger"
                  type="button"
                  disabled={changingLibrary || practiceBusy}
                  onClick={() => deleteFolderDialog.current?.showModal()}
                >
                  Delete folder
                </button>
              </div>
            )}
          </div>
        )}
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
              {attempts.map(({ id, completedAt, score, name }) => (
                <li key={id}>
                  <button
                    type="button"
                    aria-pressed={selectedId === id}
                    onClick={() => {
                      setPlaybackError(false)
                      setSelectedId(id)
                    }}
                  >
                    <span className="ph-menu-attempt-title">
                      {name && <strong>{name}</strong>}
                      <time dateTime={new Date(completedAt).toISOString()}>
                        {recordingDate.format(completedAt)}
                      </time>
                    </span>
                    <span>
                      {score && `${score1(score.points)}/${max1(score.maximum)} · `}
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
              <h2 id="ph-preview-heading">
                {selected.name ?? recordingDate.format(selected.completedAt)}
              </h2>
            </div>
            {deletingId === selected.id ? (
              <p role="status">Deleting…</p>
            ) : selectedRecording ? (
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
            <div className="ph-menu-attempt-actions">
              {library.folders.length > 0 && (
                <FolderSelect
                  folders={library.folders}
                  label="Move to folder"
                  value={selected.folderId}
                  onChange={(id) => void moveAttempt(selected, id)}
                  disabled={changingLibrary}
                />
              )}
              <button
                className="ph-script-button"
                type="button"
                disabled={changingLibrary}
                onClick={() => openNameDialog('renameAttempt', selected.name ?? '')}
              >
                Rename
              </button>
              {/* without a score there'd be nothing left to keep, so only the session can be deleted */}
              {selectedRecording && selected.score && (
                <button
                  className="ph-menu-secondary ph-menu-danger"
                  type="button"
                  disabled={practiceBusy || changingLibrary}
                  onClick={() => void deleteSaved(selected, true)}
                >
                  Delete video
                </button>
              )}
              <button
                className="ph-menu-secondary ph-menu-danger"
                type="button"
                disabled={practiceBusy || changingLibrary}
                onClick={() => void deleteSaved(selected, false)}
              >
                Delete session
              </button>
            </div>
            {deleteError?.id === selected.id && <p role="status">{deleteError.text}</p>}
          </section>
        )}
      </div>
      <button
        type="button"
        className="ph-fab"
        aria-label="New folder"
        title="New folder"
        disabled={libraryLoading || libraryError}
        onClick={() => openNameDialog('create')}
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          aria-hidden="true"
        >
          <path d="M12 5v14M5 12h14" />
        </svg>
      </button>
      <dialog
        ref={newFolderDialog}
        className="ph-dialog"
        aria-labelledby="ph-new-folder-heading"
        onClick={(event) => {
          // a click on the backdrop (outside the box) closes it
          if (event.target === event.currentTarget) event.currentTarget.close()
        }}
      >
        <form onSubmit={(event) => void submitName(event)}>
          <h2 id="ph-new-folder-heading">
            {nameMode === 'create'
              ? 'New folder'
              : nameMode === 'renameFolder'
                ? 'Rename folder'
                : 'Rename attempt'}
          </h2>
          <p>
            {nameMode === 'renameAttempt'
              ? 'Leave it empty to show the date instead.'
              : 'Group practice runs of the same presentation.'}
          </p>
          <label className="ph-dialog-field">
            {nameMode === 'renameAttempt' ? 'Attempt name' : 'Folder name'}
            <input
              required={nameMode !== 'renameAttempt'}
              autoFocus
              maxLength={80}
              value={folderName}
              placeholder={
                nameMode === 'renameAttempt' && selected
                  ? recordingDate.format(selected.completedAt)
                  : 'e.g. Q3 review deck'
              }
              onChange={(event) => {
                setFolderName(event.target.value)
                setCreateError('')
              }}
              disabled={changingLibrary}
            />
          </label>
          {createError && (
            <p className="ph-dialog-error" role="alert">
              {createError}
            </p>
          )}
          <div className="ph-dialog-actions">
            <button
              type="button"
              className="ph-dialog-cancel"
              onClick={() => newFolderDialog.current?.close()}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="ph-dialog-submit"
              disabled={(!folderName.trim() && nameMode !== 'renameAttempt') || changingLibrary}
            >
              {nameMode === 'create' ? 'Create folder' : 'Save'}
            </button>
          </div>
        </form>
      </dialog>
      <dialog
        ref={deleteFolderDialog}
        className="ph-dialog"
        aria-labelledby="ph-delete-folder-heading"
        onClick={(event) => {
          if (event.target === event.currentTarget) event.currentTarget.close()
        }}
      >
        <form method="dialog">
          <h2 id="ph-delete-folder-heading">Delete “{currentFolder?.name}”?</h2>
          <p>
            {currentFolderAttempts.length === 0
              ? 'This folder is empty.'
              : `It has ${currentFolderAttempts.length} practice attempt${
                  currentFolderAttempts.length === 1 ? '' : 's'
                }. Keep them in Unfiled, or delete them with their recordings and scores.`}
          </p>
          <div className="ph-dialog-actions">
            <button type="submit" className="ph-dialog-cancel">
              Cancel
            </button>
            {currentFolderAttempts.length > 0 && (
              <button
                type="button"
                className="ph-dialog-secondary"
                disabled={changingLibrary}
                onClick={() => void deleteFolder(false)}
              >
                Keep attempts
              </button>
            )}
            <button
              type="button"
              className="ph-dialog-danger"
              disabled={changingLibrary}
              onClick={() => void deleteFolder(true)}
            >
              {currentFolderAttempts.length > 0 ? 'Delete folder and attempts' : 'Delete folder'}
            </button>
          </div>
        </form>
      </dialog>
    </main>
  )
}
