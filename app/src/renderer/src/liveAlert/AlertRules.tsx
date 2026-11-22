import { useRef } from 'react'
import {
  groups,
  ruleInfo,
  rules,
  type Preferences,
  type Rule,
  type RuleSettings
} from './ruleDefinitions'
import { RULE_SCORING } from './scoringWeights'

type Props = {
  selected: Rule
  settingsByRule: Preferences['rules']
  onSelect: (rule: Rule) => void
  updateRule: (id: Rule['id'], change: Partial<RuleSettings>) => void
}

/** Each rule is a row: clicking the row opens its settings and scoring in a pop-up; the switch on
 *  the right only turns the alert on or off. */
export default function AlertRules({
  selected,
  settingsByRule,
  onSelect,
  updateRule
}: Props): React.JSX.Element {
  const dialog = useRef<HTMLDialogElement>(null)
  const settings = settingsByRule[selected.id]
  const info = ruleInfo(selected)
  const scoring = RULE_SCORING[selected.id]

  function open(rule: Rule): void {
    onSelect(rule)
    dialog.current?.showModal()
  }

  function summary(rule: Rule): string {
    const ruleSettings = settingsByRule[rule.id]
    const details = ruleInfo(rule)
    if (details.unit !== undefined) return `${ruleSettings.threshold} ${details.unit}`
    return details.instant ? 'Alerts each time' : `After ${ruleSettings.duration}s`
  }

  return (
    <section className="la-card" aria-labelledby="la-rules">
      <h2 id="la-rules">Alert and scoring settings</h2>
      <p className="la-hint">
        Click a rule to adjust it and see what it&apos;s worth in your score.
      </p>
      <div className="la-rules">
        {groups.map((group) => (
          <div key={group} role="group" aria-label={group}>
            <h3 className="la-group">{group}</h3>
            {rules
              .filter((rule) => rule.group === group)
              .map((rule) => {
                const points = RULE_SCORING[rule.id]?.points
                return (
                  <div className="la-rule" key={rule.id}>
                    <button
                      className="la-rule-open"
                      type="button"
                      aria-haspopup="dialog"
                      onClick={() => open(rule)}
                    >
                      <span className="la-rule-text">
                        <span className="la-rule-name">{rule.name}</span>
                        <span className="la-rule-meta">{summary(rule)}</span>
                      </span>
                      <span className="la-rule-points">
                        {points === null || points === undefined ? 'Not scored' : `${points} pts`}
                      </span>
                      <svg
                        className="la-rule-chevron"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        aria-hidden="true"
                      >
                        <path d="M9 6l6 6-6 6" />
                      </svg>
                    </button>
                    <input
                      type="checkbox"
                      role="switch"
                      aria-label={`Enable ${rule.name.toLowerCase()}`}
                      checked={settingsByRule[rule.id].enabled}
                      onChange={(event) => updateRule(rule.id, { enabled: event.target.checked })}
                    />
                  </div>
                )
              })}
          </div>
        ))}
      </div>

      <dialog
        ref={dialog}
        className="ph-dialog la-rule-dialog"
        aria-labelledby="la-rule-dialog-heading"
        onClick={(event) => {
          // a click on the backdrop (outside the box) closes it
          if (event.target === event.currentTarget) event.currentTarget.close()
        }}
      >
        <form method="dialog">
          <div className="la-dialog-head">
            <h2 id="la-rule-dialog-heading">{selected.name}</h2>
            <label className="la-dialog-switch">
              {settings.enabled ? 'On' : 'Off'}
              <input
                type="checkbox"
                role="switch"
                aria-label={`Enable ${selected.name.toLowerCase()}`}
                checked={settings.enabled}
                onChange={(event) => updateRule(selected.id, { enabled: event.target.checked })}
              />
            </label>
          </div>
          <p>
            Alert: <span className="la-dialog-cue">{selected.cue}</span>
          </p>

          <fieldset className="la-dialog-fields" disabled={!settings.enabled}>
            {info.unit !== undefined && (
              <label>
                {info.label ?? 'Threshold'}{' '}
                <span>
                  {settings.threshold} {info.unit}
                </span>
                <input
                  type="range"
                  min={info.min}
                  max={info.max}
                  step={info.step ?? 1}
                  value={settings.threshold ?? info.value}
                  onChange={(event) =>
                    updateRule(selected.id, { threshold: event.target.valueAsNumber })
                  }
                />
              </label>
            )}
            {!info.instant && (
              <label>
                Minimum duration <span>{settings.duration}s</span>
                <input
                  type="range"
                  min="0.1"
                  max="15"
                  step="0.1"
                  value={settings.duration}
                  onChange={(event) =>
                    updateRule(selected.id, { duration: event.target.valueAsNumber })
                  }
                />
              </label>
            )}
            <label>
              Cooldown <span>{settings.cooldown}s</span>
              <input
                type="range"
                min="0"
                max="120"
                value={settings.cooldown}
                onChange={(event) =>
                  updateRule(selected.id, { cooldown: event.target.valueAsNumber })
                }
              />
            </label>
          </fieldset>

          {scoring && (
            <section className="la-dialog-scoring" aria-label="Scoring">
              <h3>Scoring</h3>
              <p>
                {scoring.points === null
                  ? scoring.category
                  : `Worth ${scoring.points} of 100 points · ${scoring.category}`}
              </p>
              {scoring.note && <p className="la-dialog-note">{scoring.note}</p>}
              {settings.enabled ? null : (
                <p className="la-dialog-note">
                  While this alert is off, it&apos;s left out of the score.
                </p>
              )}
            </section>
          )}

          <div className="ph-dialog-actions">
            <button type="submit" className="ph-dialog-submit">
              Done
            </button>
          </div>
        </form>
      </dialog>
    </section>
  )
}
