import { useEffect, useState } from 'react'
import AlertPreview from './AlertPreview'
import AlertRules from './AlertRules'
import WebcamDisplay from './WebcamDisplay'
import { rules, type Rule, type RuleSettings, type Style } from './ruleDefinitions'
import { useAlertPreferences } from './useAlertPreferences'
import './liveAlerts.css'

export default function Settings(): React.JSX.Element {
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
          <p className="la-product">Colloquia</p>
          <h1>Settings</h1>
          <p>Choose what to watch for and what to show while you practice.</p>
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
          <div className="la-column">
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
            <WebcamDisplay
              showSkeleton={preferences.showSkeleton}
              showStats={preferences.showStats}
              skeletonParts={preferences.skeletonParts}
              statParts={preferences.statParts}
              onChange={(change) => setPreferences((current) => ({ ...current, ...change }))}
            />
          </div>
        </div>
      </div>
    </main>
  )
}
