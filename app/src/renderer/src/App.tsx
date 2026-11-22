import { useEffect, useState } from 'react'
import type { PracticeLibrary } from '../../main/practiceLibrary'
import LiveAlerts from './liveAlert/LiveAlerts'
import MainMenu from './menu/MainMenu'
import Practice from './practice/Practice'
import './practice/practice.css'

const emptyLibrary: PracticeLibrary = { folders: [], attempts: [] }

function App(): React.JSX.Element {
  const [page, setPage] = useState<'menu' | 'practice' | 'alerts'>('menu')
  const [practice, setPractice] = useState<{ key: number; folderId: string | null } | null>(null)
  const [practiceBusy, setPracticeBusy] = useState(false)
  const [folderId, setFolderId] = useState<string | null>(null)
  const [library, setLibrary] = useState<PracticeLibrary | null>(null)
  const [libraryError, setLibraryError] = useState(false)

  useEffect(() => {
    let cancelled = false
    window.api.practiceLibrary.list().then(
      (loaded) => {
        if (!cancelled) setLibrary((current) => current ?? loaded)
      },
      () => {
        if (!cancelled) setLibraryError(true)
      }
    )
    return () => {
      cancelled = true
    }
  }, [])

  function openPractice(newSession = false): void {
    setPractice((current) => {
      if (current && (!newSession || practiceBusy)) return current
      return { key: (current?.key ?? -1) + 1, folderId }
    })
    setPage('practice')
  }

  return (
    <div className="ph-app">
      <header className="ph-bar">
        <div className="ph-brand">
          <span aria-hidden="true" />
          Presentation Helper
        </div>
        <nav aria-label="Main navigation">
          <button
            aria-current={page === 'menu' ? 'page' : undefined}
            onClick={() => setPage('menu')}
          >
            Main Menu
          </button>
          <button
            aria-current={page === 'practice' ? 'page' : undefined}
            onClick={() => openPractice()}
          >
            Practice
          </button>
          <button
            aria-current={page === 'alerts' ? 'page' : undefined}
            onClick={() => setPage('alerts')}
          >
            Live alerts
          </button>
        </nav>
      </header>
      {page === 'menu' && (
        <MainMenu
          practiceBusy={practiceBusy}
          onPractice={() => openPractice(true)}
          library={library ?? emptyLibrary}
          libraryLoading={library === null && !libraryError}
          libraryError={library === null && libraryError}
          onLibraryChange={setLibrary}
          folderId={folderId}
          onFolderChange={setFolderId}
        />
      )}
      <div hidden={page !== 'practice'}>
        {practice && (
          <Practice
            key={practice.key}
            visible={page === 'practice'}
            onBusyChange={setPracticeBusy}
            folders={(library ?? emptyLibrary).folders}
            initialFolderId={practice.folderId}
            onLibraryChange={setLibrary}
            onFolderChange={setFolderId}
          />
        )}
      </div>
      <div hidden={page !== 'alerts'}>{page === 'alerts' && <LiveAlerts />}</div>
    </div>
  )
}

export default App
