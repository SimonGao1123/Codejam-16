import type { SessionScoreMessage } from '../audio/audioProtocol'
import { max1, score1 } from '../scoreFormat'

const CATEGORY_LABELS = {
  filler: 'Filler words',
  voice_tone: 'Voice tone',
  speaking_pace: 'Speaking pace',
  body_language: 'Body language',
  script_accuracy: 'Script accuracy',
  time_limit: 'Time limit'
} as const

const VOICE_LABELS = { pitch: 'Pitch swings', volume: 'Volume spikes', quiet: 'Low volume' }

function clock(minutes: number): string {
  const seconds = Math.round(minutes * 60)
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

export default function SessionResults({
  result
}: {
  result: SessionScoreMessage
}): React.JSX.Element {
  return (
    <section className="ph-results" aria-labelledby="ph-results-heading" aria-live="polite">
      <div className="ph-card-head">
        <h2 id="ph-results-heading">Practice results</h2>
        {result.score !== null && <strong>{score1(result.score)}%</strong>}
      </div>
      {result.score === null ? (
        <p>No score is available. Scoring requires an enabled check with enough practice data.</p>
      ) : (
        <>
          <dl className="ph-results-breakdown">
            {Object.entries(result.categories).map(([category, breakdown]) => {
              const pace = breakdown.checks.moderate_pace
              const time = breakdown.checks.over_time
              return (
                <div key={category}>
                  <dt>{CATEGORY_LABELS[category as keyof typeof CATEGORY_LABELS]}</dt>
                  <dd>
                    {breakdown.points === null
                      ? 'Not measured'
                      : `${score1(breakdown.points)} / ${max1(breakdown.max_points)}`}
                    {breakdown.points !== null &&
                      breakdown.weight !== undefined &&
                      breakdown.max_points < breakdown.weight && (
                        <small> · {max1(breakdown.weight)} with every check on</small>
                      )}
                    {pace?.average_wpm !== undefined && (
                      <small>
                        {' '}
                        · {pace.average_wpm} WPM (target {pace.target_wpm} WPM)
                      </small>
                    )}
                    {time?.recorded_minutes !== undefined && time.limit_minutes !== undefined && (
                      <small>
                        {' '}
                        · {clock(time.recorded_minutes)} of {clock(time.limit_minutes)}
                        {time.over_percent ? `, ${Math.round(time.over_percent)}% over` : ''}
                      </small>
                    )}
                  </dd>
                </div>
              )
            })}
          </dl>
          {result.categories.voice_tone.points !== null && (
            <dl className="ph-results-breakdown" aria-label="Voice tone checks">
              {Object.entries(VOICE_LABELS).map(([id, label]) => {
                const check = result.categories.voice_tone.checks[id]
                if (!check) return null
                return (
                  <div key={id}>
                    <dt>{label}</dt>
                    <dd>
                      {score1(check.max_points - check.deduction)} / {max1(check.max_points)}
                      <small> · {check.count} {check.count === 1 ? 'violation' : 'violations'}</small>
                    </dd>
                  </div>
                )
              })}
            </dl>
          )}
          <p>
            {result.measured_points < 100
              ? `Scored out of the ${max1(result.measured_points)} of 100 points that were measured; criteria that weren’t measured are left out.`
              : 'Scored out of all 100 points.'}
          </p>
          {result.feedback.length > 0 ? (
            <ul>
              {result.feedback.map(({ category, rule, message }) => (
                <li key={`${category}:${rule}`}>{message}</li>
              ))}
            </ul>
          ) : (
            <p>No deductions for the checks that were measured.</p>
          )}
        </>
      )}
    </section>
  )
}
