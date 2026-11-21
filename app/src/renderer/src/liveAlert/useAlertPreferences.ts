import { useSyncExternalStore } from 'react'
import {
  rules,
  styles,
  ruleInfo,
  type Preferences,
  type Rule,
  type RuleSettings
} from './ruleDefinitions'

// v2: the rule list now matches the alerts the practice session shows (v1 had 4 voice/posture rules)
const storageKey = 'presentation-helper:live-alerts:v2'

function defaults(): Preferences {
  return {
    style: 'banner',
    rules: Object.fromEntries(
      rules.map((rule) => [rule.id, defaultRule(rule)])
    ) as Preferences['rules']
  }
}

function defaultRule(rule: Rule): RuleSettings {
  return {
    enabled: true,
    threshold: ruleInfo(rule).value ?? null,
    duration: rule.duration,
    cooldown: rule.cooldown
  }
}

function validThreshold(rule: Rule, threshold: unknown): boolean {
  const { min, max } = ruleInfo(rule)
  if (min === undefined || max === undefined) return threshold === null
  return typeof threshold === 'number' && threshold >= min && threshold <= max
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
          validThreshold(rule, entry.threshold) &&
          entry.duration >= 0 &&
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

// One shared copy for the whole app, so a change on the Live alerts tab reaches the practice
// session (which stays mounted in the background) straight away.
let current = readPreferences()
const listeners = new Set<() => void>()

function setPreferences(update: Preferences | ((current: Preferences) => Preferences)): void {
  current = typeof update === 'function' ? update(current) : update
  try {
    localStorage.setItem(storageKey, JSON.stringify(current))
  } catch {
    // Settings still work for the current window.
  }
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useAlertPreferences(): readonly [Preferences, typeof setPreferences] {
  const preferences = useSyncExternalStore(subscribe, () => current)
  return [preferences, setPreferences] as const
}
