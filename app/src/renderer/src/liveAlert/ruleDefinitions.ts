export const rules = [
  {
    id: 'volume',
    name: 'Volume spike',
    unit: 'dB above normal',
    min: 3,
    max: 18,
    value: 8,
    duration: 0.3,
    cooldown: 3,
    cue: 'Lower your volume'
  },
  {
    id: 'pitch',
    name: 'Pitch spike',
    unit: 'semitones above normal',
    min: 3,
    max: 18,
    value: 9,
    duration: 0.2,
    cooldown: 3,
    cue: 'Steady your pitch'
  },
  {
    id: 'quiet',
    name: 'Low volume',
    unit: 'dB below normal',
    min: 5,
    max: 30,
    value: 15,
    duration: 3,
    cooldown: 3,
    cue: 'Speak up'
  },
  {
    id: 'posture',
    name: 'Posture drift',
    unit: '% from baseline',
    min: 5,
    max: 40,
    value: 15,
    duration: 3,
    cooldown: 30,
    cue: 'Sit up straight'
  }
] as const

export type Rule = (typeof rules)[number]
export type RuleSettings = {
  enabled: boolean
  threshold: number
  duration: number
  cooldown: number
}
export type Style = 'banner' | 'compact' | 'silent'
export type Preferences = { style: Style; rules: Record<Rule['id'], RuleSettings> }

export const styles = [
  { id: 'banner', label: 'Banner' },
  { id: 'compact', label: 'Compact' },
  { id: 'silent', label: 'Silent' }
] as const
