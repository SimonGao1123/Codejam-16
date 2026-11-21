import { rules, type Preferences, type Rule, type RuleSettings } from './ruleDefinitions'

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
  return (
    <section className="la-card" aria-labelledby="la-rules">
      <h2 id="la-rules">Alert rules</h2>
      <div className="la-rules">
        {rules.map((rule) => (
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
      <div className="la-fields">
        <h3>{selected.name} settings</h3>
        <label>
          Threshold{' '}
          <span>
            {settings.threshold} {selected.unit}
          </span>
          <input
            type="range"
            min={selected.min}
            max={selected.max}
            value={settings.threshold}
            onChange={(event) => updateRule(selected.id, { threshold: event.target.valueAsNumber })}
          />
        </label>
        <div className="la-timing">
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
      </div>
    </section>
  )
}
