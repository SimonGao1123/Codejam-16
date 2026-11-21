import { useState } from 'react'
import LiveAlerts from './liveAlert/LiveAlerts'
import MainMenu from './menu/MainMenu'
import Practice from './practice/Practice'
import './practice/practice.css'

function App(): React.JSX.Element {
  const [page, setPage] = useState<'menu' | 'practice' | 'alerts'>('menu')
  const [practiceKey, setPracticeKey] = useState<number | null>(null)
  const [practiceBusy, setPracticeBusy] = useState(false)

  function openPractice(newSession = false): void {
    setPracticeKey((key) => {
      if (key === null) return 0
      return newSession && !practiceBusy ? key + 1 : key
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
        <MainMenu practiceBusy={practiceBusy} onPractice={() => openPractice(true)} />
      )}
      <div hidden={page !== 'practice'}>
        {practiceKey !== null && (
          <Practice key={practiceKey} visible={page === 'practice'} onBusyChange={setPracticeBusy} />
        )}
      </div>
      <div hidden={page !== 'alerts'}>{page === 'alerts' && <LiveAlerts />}</div>
    </div>
  )
}

export default App
