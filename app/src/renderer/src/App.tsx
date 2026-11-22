import { useEffect, useState } from 'react'
import type { PracticeLibrary } from '../../main/practiceLibrary'
import VoiceCalibration from './calibration/VoiceCalibration'
import { loadVoiceCalibration, saveVoiceCalibration } from './calibration/calibrationCapture'
import LiveAlerts from './liveAlert/LiveAlerts'
import MainMenu from './menu/MainMenu'
import Practice from './practice/Practice'
import UploadGrade from './upload/UploadGrade'
import wordmark from './assets/colloquia-wordmark.png'
import './theme.css'
import './practice/practice.css'

const emptyLibrary: PracticeLibrary = { folders: [], attempts: [] }

function App(): React.JSX.Element {
  const [page, setPage] = useState<'menu' | 'practice' | 'upload' | 'alerts' | 'calibration'>(
    'menu'
  )
  const [voiceBaseline, setVoiceBaseline] = useState(loadVoiceCalibration)
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
      <nav className="ph-rail" aria-label="Main navigation">
        <img className="ph-brand" src={wordmark} alt="Colloquia" />
        <div className="ph-rail-sep" />
        <button aria-current={page === 'menu' ? 'page' : undefined} onClick={() => setPage('menu')}>
          <RailIcon d="M3 11l9-7 9 7M5 10v10h5v-6h4v6h5V10" />
          Home
        </button>
        <button
          aria-current={page === 'practice' ? 'page' : undefined}
          onClick={() => openPractice()}
        >
          <RailIcon d="M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM5 11a7 7 0 0 0 14 0M12 18v3" />
          Practice
        </button>
        <button
          aria-current={page === 'upload' ? 'page' : undefined}
          onClick={() => setPage('upload')}
        >
          <RailIcon d="M12 15V4M7 9l5-5 5 5M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" />
          Upload
        </button>
        <button
          aria-current={page === 'calibration' ? 'page' : undefined}
          disabled={practiceBusy}
          onClick={() => setPage('calibration')}
        >
          <RailIcon d="M4 12h2M8 8v8M12 5v14M16 8v8M20 12h0" />
          Voice
        </button>
        <button
          aria-current={page === 'alerts' ? 'page' : undefined}
          onClick={() => setPage('alerts')}
        >
          <RailIcon d="M6 8a6 6 0 0 1 12 0c0 7 3 8 3 8H3s3-1 3-8M10 20a2 2 0 0 0 4 0" />
          Alerts
        </button>
      </nav>
      <div className="ph-page">
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
              voiceBaseline={voiceBaseline}
              onOpenCalibration={() => setPage('calibration')}
            />
          )}
        </div>
        <div hidden={page !== 'upload'}>{page === 'upload' && <UploadGrade />}</div>
        <div hidden={page !== 'alerts'}>{page === 'alerts' && <LiveAlerts />}</div>
        {page === 'calibration' && (
          <VoiceCalibration
            baseline={voiceBaseline}
            onSave={(baseline) => {
              saveVoiceCalibration(baseline)
              setVoiceBaseline(baseline)
            }}
          />
        )}
      </div>
    </div>
  )
}

function RailIcon({ d }: { d: string }): React.JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  )
}

export default App
