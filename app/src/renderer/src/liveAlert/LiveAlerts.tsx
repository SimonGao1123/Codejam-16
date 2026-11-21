import { useEffect, useState } from 'react'
import AlertPreview from './AlertPreview'
import AlertRules from './AlertRules'
import { rules, type Rule, type RuleSettings, type Style } from './ruleDefinitions'
import { useAlertPreferences } from './useAlertPreferences'
import './liveAlerts.css'

export default function LiveAlerts(): React.JSX.Element {
  const [preferences, setPreferences] = useAlertPreferences()
  const [selected, setSelected] = useState<Rule>(rules[0])
  const [preview, setPreview] = useState(false)
  const settings = preferences.rules[selected.id]

  useEffect(() => {
    if (!preview) return
    const timer = window.setTimeout(() => setPreview(false), 3000)
    return () => window.clearTimeout(timer)
  }, [preview])

  function updateRule(id: Rule['id'], change: Partial<RuleSettings>): void {
    setPreview(false)
    setPreferences((current) => ({
      ...current,
      rules: { ...current.rules, [id]: { ...current.rules[id], ...change } }
    }))
  }

  return (
    <main className="la">
      <div className="la-shell">
        <header className="la-header">
          <p className="la-product">Presentation Helper</p>
          <h1>Live alerts</h1>
          <p>Choose what to watch for while you practice.</p>
        </header>
        <div className="la-layout">
          <AlertRules
            selected={selected}
            settingsByRule={preferences.rules}
            onSelect={(rule) => {
              setSelected(rule)
              setPreview(false)
            }}
            updateRule={updateRule}
          />
          <AlertPreview
            cue={selected.cue}
            enabled={settings.enabled}
            preview={preview}
            style={preferences.style}
            onStyleChange={(style: Style) => {
              setPreview(false)
              setPreferences((current) => ({ ...current, style }))
            }}
            onPreview={() => setPreview(true)}
          />
        </div>
      </div>
    </main>
  )
}
