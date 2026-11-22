// What each alert's check is worth in the end-of-session score (fixed weights out of 100). Keep in
// sync with service/presentation_service/scoring.py (CATEGORY_WEIGHTS, the rule tables and
// CHECK_ALERTS: turning an alert off leaves its check out of the score).

export type RuleScoring = {
  points: number | null // null = not scored on its own
  category: string
  note?: string
}

export const RULE_SCORING: Record<string, RuleScoring> = {
  volume: { points: 5, category: 'Voice tone (15)' },
  pitch: { points: 5, category: 'Voice tone (15)' },
  quiet: { points: 5, category: 'Voice tone (15)' },
  pace: { points: 5, category: 'Speaking pace (10)', note: 'Steady pace from part to part.' },
  pace_avg: {
    points: 5,
    category: 'Speaking pace (10)',
    note: 'Average pace close to your desired pace.'
  },
  pause: { points: null, category: 'Alert only', note: 'Long pauses aren’t scored.' },
  tilt: { points: 2.5, category: 'Body language (25)' },
  look_down: { points: 4, category: 'Body language (25)' },
  face: {
    points: 6,
    category: 'Body language (25)',
    note: 'A second time out of frame can cost these points again.'
  },
  hands_missing: {
    points: null,
    category: 'Body language (25)',
    note: 'Hands dropping out of view lowers the hand checks’ points.'
  },
  still: { points: 5, category: 'Body language (25)' },
  fast: { points: 4, category: 'Body language (25)' },
  repeat: { points: 3.5, category: 'Body language (25)' },
  filler: { points: 15, category: 'Filler words (15)', note: 'Scored per minute of speaking.' },
  off_script: {
    points: 5,
    category: 'Script accuracy (25)',
    note: '1 point per stretch matching none of your script. Turning it off also leaves out script coverage (15).'
  },
  out_of_order: {
    points: 5,
    category: 'Script accuracy (25)',
    note: '1 point per skip ahead or step back. Turning it off also leaves out script coverage (15).'
  },
  stuck: {
    points: null,
    category: 'Script accuracy (25)',
    note: 'Not scored on its own. Turning it off leaves out script coverage (15).'
  }
}
