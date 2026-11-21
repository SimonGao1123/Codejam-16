import { useEffect, useState } from 'react'
import { rules, styles, type Preferences, type Rule, type RuleSettings } from './ruleDefinitions'

const storageKey = 'presentation-helper:live-alerts:v1'

function defaults(): Preferences {
  return {
    style: 'banner',
    rules: {
      volume: defaultRule(rules[0]),
      pitch: defaultRule(rules[1]),
      quiet: defaultRule(rules[2]),
      posture: defaultRule(rules[3])
    }
  }
}

function defaultRule(rule: Rule): RuleSettings {
  return { enabled: true, threshold: rule.value, duration: rule.duration, cooldown: rule.cooldown }
}

function readPreferences(): Preferences {
  const preferences = defaults()
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) ?? 'null') as Preferences | null
    if (saved && styles.some((style) => style.id === saved.style)) {
      preferences.style = saved.style
      for (const rule of rules) {
        const entry = saved.rules?.[rule.id]
        if (
          entry &&
          typeof entry.enabled === 'boolean' &&
          entry.threshold >= rule.min &&
          entry.threshold <= rule.max &&
          entry.duration >= 0.1 &&
          entry.duration <= 15 &&
          entry.cooldown >= 0 &&
          entry.cooldown <= 120
        ) {
          preferences.rules[rule.id] = entry
        }
      }
    }
  } catch {
    // Fall back to defaults when browser storage is unavailable.
  }
  return preferences
}

export function useAlertPreferences() {
  const [preferences, setPreferences] = useState(readPreferences)

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(preferences))
    } catch {
      // Settings still work for the current window.
    }
  }, [preferences])

  return [preferences, setPreferences] as const
}
