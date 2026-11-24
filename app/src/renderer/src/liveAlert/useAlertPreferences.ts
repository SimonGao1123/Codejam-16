import { useSyncExternalStore } from 'react'
import {
  rules,
  styles,
  ruleInfo,
  type Preferences,
  type Rule,
  type RuleSettings
} from './ruleDefinitions'
import { readParts, skeletonParts, statParts } from './webcamParts'

const storageKey = 'presentation-helper:live-alerts:v6'
const handsLegacyStorageKey = 'presentation-helper:live-alerts:v5'
const lastStorageKey = 'presentation-helper:live-alerts:v4'
const previousStorageKey = 'presentation-helper:live-alerts:v3'
const legacyStorageKey = 'presentation-helper:live-alerts:v2'

function defaults(): Preferences {
  return {
    style: 'banner',
    showSkeleton: false,
    showStats: false,
    skeletonParts: readParts(skeletonParts),
    statParts: readParts(statParts),
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
    const current = localStorage.getItem(storageKey)
    const handsLegacy = localStorage.getItem(handsLegacyStorageKey)
    const previous = localStorage.getItem(lastStorageKey) ?? localStorage.getItem(previousStorageKey)
    const saved = JSON.parse(current ?? handsLegacy ?? previous ?? localStorage.getItem(legacyStorageKey) ?? 'null') as
      | Preferences
      | null
    if (saved && styles.some((style) => style.id === saved.style)) {
      preferences.style = saved.style
      preferences.showSkeleton = saved.showSkeleton === true
      preferences.showStats = saved.showStats === true
      preferences.skeletonParts = readParts(skeletonParts, saved.skeletonParts)
      preferences.statParts = readParts(statParts, saved.statParts)
      for (const rule of rules) {
        const savedEntry = saved.rules?.[rule.id]
        // The first pace rule shipped with a hidden 15 s cooldown. It now has a
        // visible, responsive 5 s default, so migrate that old default once.
        // Volume spikes were too sensitive at 6 dB for 0.3 s; move that old default to the new one
        // (a value the user picked stays).
        const entry =
          rule.id === 'pace' && savedEntry?.cooldown === 15
            ? { ...savedEntry, cooldown: 5 }
            : rule.id === 'volume' && savedEntry?.threshold === 6 && savedEntry.duration === 0.3
              ? { ...savedEntry, threshold: rule.value, duration: rule.duration }
              : savedEntry
        if (
          entry &&
          typeof entry.enabled === 'boolean' &&
          validThreshold(rule, entry.threshold) &&
          entry.duration >= 0 &&
          entry.duration <= 15 &&
          entry.cooldown >= 0 &&
          entry.cooldown <= 120
        ) {
          const settings = { ...entry }
          if (current === null && rule.id === 'hands_missing' && entry.duration === 1.5) {
            settings.duration = rule.duration
          }
          if (current === null && handsLegacy === null && rule.id === 'pitch') {
            const oldDefault =
              (entry.threshold === 4 && entry.duration === 0.1) ||
              (entry.threshold === 5 && entry.duration === 0.2) ||
              (previous === null && entry.threshold === 9 && entry.duration === 0.2)
            if (oldDefault) {
              settings.threshold = rule.value
              settings.duration = rule.duration
            }
          }
          if (current === null && handsLegacy === null && previous === null) {
            if (rule.id === 'volume' && entry.threshold === 8) settings.threshold = rule.value
            if (rule.id === 'quiet') {
              if (entry.threshold === 15) settings.threshold = rule.value
              if (entry.duration === 3) settings.duration = rule.duration
            }
          }
          preferences.rules[rule.id] = settings
        }
      }
    }
  } catch {
    // Fall back to defaults when browser storage is unavailable.
  }
  return preferences
}

// One shared copy for the whole app, so a change on the Settings tab reaches the practice
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
