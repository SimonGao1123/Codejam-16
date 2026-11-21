import { useState } from 'react'
import LiveAlerts from './liveAlert/LiveAlerts'
import Practice from './practice/Practice'
import './practice/practice.css'

function App(): React.JSX.Element {
  const [page, setPage] = useState<'practice' | 'alerts'>('practice')

  return (
    <div className="ph-app">
      <header className="ph-bar">
        <div className="ph-brand">
          <span aria-hidden="true" />
          Presentation Helper
        </div>
        <nav aria-label="Main navigation">
          <button
            aria-current={page === 'practice' ? 'page' : undefined}
            onClick={() => setPage('practice')}
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
      <div hidden={page !== 'practice'}>
        <Practice />
      </div>
      <div hidden={page !== 'alerts'}>{page === 'alerts' && <LiveAlerts />}</div>
    </div>
  )
}

export default App
