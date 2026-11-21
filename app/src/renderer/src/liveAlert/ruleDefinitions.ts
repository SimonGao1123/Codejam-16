// One rule per alert the practice session can show. The Python analysis service applies these
// settings (enabled, threshold, minimum duration, cooldown; see the `settings` message in
// docs/webcam-protocol.md) and the overlay draws the cue in the chosen style.
// `cue` is the exact text that pops up. Defaults match the service's built-in values.

type RuleDefinition = {
  id: string
  group: 'Voice' | 'Posture' | 'Hands' | 'Speech' | 'Script'
  name: string
  cue: string
  duration: number // seconds the problem has to last before the alert shows
  cooldown: number // seconds before the same alert can show again
  instant?: boolean // fires on a single event (a filler word): no minimum duration
  // threshold slider; rules without one just turn on/off
  unit?: string
  min?: number
  max?: number
  step?: number
  value?: number
}

export const rules = [
  {
    id: 'volume',
    group: 'Voice',
    name: 'Volume spike',
    cue: 'Steady your volume',
    unit: 'dB above normal',
    min: 3,
    max: 18,
    value: 8,
    duration: 0.3,
    cooldown: 3
  },
  {
    id: 'pitch',
    group: 'Voice',
    name: 'Pitch spike',
    cue: 'Steady your pitch',
    unit: 'semitones above normal',
    min: 3,
    max: 18,
    value: 9,
    duration: 0.2,
    cooldown: 3
  },
  {
    id: 'quiet',
    group: 'Voice',
    name: 'Low volume',
    cue: 'Speak up',
    unit: 'dB below normal',
    min: 5,
    max: 30,
    value: 15,
    duration: 3,
    cooldown: 3
  },
  {
    id: 'tilt',
    group: 'Posture',
    name: 'Shoulder tilt',
    cue: 'Level your shoulders',
    unit: '° from calibrated',
    min: 2,
    max: 20,
    value: 6,
    duration: 1.5,
    cooldown: 0
  },
  {
    id: 'look_down',
    group: 'Posture',
    name: 'Looking down',
    cue: 'Look up',
    unit: '° below calibrated',
    min: 5,
    max: 40,
    value: 15,
    duration: 1,
    cooldown: 0
  },
  {
    id: 'face',
    group: 'Posture',
    name: 'Face not visible',
    cue: 'Face the camera',
    duration: 1,
    cooldown: 0
  },
  {
    id: 'hands_missing',
    group: 'Hands',
    name: 'Hands not visible',
    cue: 'Show both hands or step back',
    duration: 1.5,
    cooldown: 0
  },
  {
    id: 'still',
    group: 'Hands',
    name: 'No hand movement',
    cue: 'Use your hands',
    duration: 10,
    cooldown: 0
  },
  {
    id: 'fast',
    group: 'Hands',
    name: 'Fast hand movement',
    cue: 'Slow your hands',
    unit: 'shoulder widths / s',
    min: 1,
    max: 6,
    step: 0.5,
    value: 3,
    duration: 0.4,
    cooldown: 0
  },
  {
    id: 'repeat',
    group: 'Hands',
    name: 'Repeated gestures',
    cue: 'Vary your gestures',
    unit: '% similar',
    min: 60,
    max: 100,
    value: 80,
    duration: 1,
    cooldown: 0
  },
  {
    id: 'filler',
    group: 'Speech',
    name: 'Filler words',
    cue: 'Filler: um',
    instant: true,
    duration: 0,
    cooldown: 0
  },
  // Script rules apply only when a script was given on the Practice page
  {
    id: 'off_script',
    group: 'Script',
    name: 'Off script',
    cue: 'Back to your script',
    unit: '% match needed (higher = stricter)',
    min: 10,
    max: 80,
    value: 35,
    duration: 6,
    cooldown: 10
  },
  {
    id: 'out_of_order',
    group: 'Script',
    name: 'Out of order',
    cue: 'Skipped ahead',
    instant: true,
    duration: 0,
    cooldown: 0
  },
  {
    id: 'stuck',
    group: 'Script',
    name: 'Stuck on one part',
    cue: 'Move on to the next part',
    unit: '× the time it takes to say it (at least 20 s)',
    min: 1.5,
    max: 8,
    step: 0.5,
    value: 3,
    instant: true,
    duration: 0,
    cooldown: 0
  }
] as const satisfies readonly RuleDefinition[]

export type Rule = (typeof rules)[number]
export type RuleId = Rule['id']
export type RuleSettings = {
  enabled: boolean
  threshold: number | null // null for rules without a threshold
  duration: number
  cooldown: number
}
export type Style = 'banner' | 'compact' | 'silent'
export type Preferences = { style: Style; rules: Record<RuleId, RuleSettings> }

export const styles = [
  { id: 'banner', label: 'Banner' },
  { id: 'compact', label: 'Compact' },
  { id: 'silent', label: 'Silent' }
] as const

export const groups = ['Voice', 'Posture', 'Hands', 'Speech', 'Script'] as const

/** Rule fields that may be missing (rules without a threshold, non-instant rules). */
export const ruleInfo = (rule: Rule): RuleDefinition => rule
