import {
  groups,
  ruleInfo,
  rules,
  type Preferences,
  type Rule,
  type RuleSettings
} from './ruleDefinitions'

type Props = {
  selected: Rule
  settingsByRule: Preferences['rules']
  onSelect: (rule: Rule) => void
  updateRule: (id: Rule['id'], change: Partial<RuleSettings>) => void
}

export default function AlertRules({
  selected,
  settingsByRule,
  onSelect,
  updateRule
}: Props): React.JSX.Element {
  const settings = settingsByRule[selected.id]
  const paceSettings = settingsByRule.pace
  const info = ruleInfo(selected)
  return (
    <section className="la-card" aria-labelledby="la-rules">
      <h2 id="la-rules">Alert and scoring settings</h2>
      <div className="la-rules">
        {groups.map((group) => (
          <div key={group} role="group" aria-label={group}>
            <h3 className="la-group">{group}</h3>
            {rules
              // Pace has a permanent control below the normal rule fields so its
              // target and alert toggle are never hidden behind a selected row.
              .filter((rule) => rule.group === group && rule.id !== 'pace')
              .map((rule) => (
                <div className="la-rule" key={rule.id} data-selected={rule.id === selected.id}>
                  <button
                    className="la-rule-name"
                    aria-pressed={rule.id === selected.id}
                    onClick={() => onSelect(rule)}
                  >
                    {rule.name}
                  </button>
                  <input
                    type="checkbox"
                    role="switch"
                    aria-label={`Enable ${rule.name.toLowerCase()}`}
                    checked={settingsByRule[rule.id].enabled}
                    onChange={(event) => updateRule(rule.id, { enabled: event.target.checked })}
                  />
                </div>
              ))}
          </div>
        ))}
      </div>
      <div className="la-fields">
        <h3>{selected.name} settings</h3>
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
        <div className="la-timing">
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
        </div>
        <section className="la-pace-settings" aria-labelledby="la-pace-settings">
          <div className="la-pace-heading">
            <h3 id="la-pace-settings">Speaking pace alert</h3>
            <input
              type="checkbox"
              role="switch"
              aria-label="Enable speaking pace alert"
              checked={paceSettings.enabled}
              onChange={(event) => updateRule('pace', { enabled: event.target.checked })}
            />
          </div>
          <label>
            Target pace <span>{paceSettings.threshold} words per minute</span>
            <input
              type="range"
              min="80"
              max="200"
              step="1"
              value={paceSettings.threshold ?? 140}
              onChange={(event) => updateRule('pace', { threshold: event.target.valueAsNumber })}
            />
          </label>
          <label className="la-pace-cooldown">
            Alert cooldown <span>{paceSettings.cooldown}s</span>
            <input
              type="range"
              min="1"
              max="30"
              step="1"
              value={paceSettings.cooldown}
              onChange={(event) => updateRule('pace', { cooldown: event.target.valueAsNumber })}
            />
          </label>
          <p>Shows “Pick up the pace” or “Slow down” outside a 40 WPM band.</p>
        </section>
      </div>
    </section>
  )
}
