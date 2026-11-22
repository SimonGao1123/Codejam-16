import type { SessionScoreMessage } from '../audio/audioProtocol'

const CATEGORY_LABELS = {
  filler: 'Filler words',
  voice_tone: 'Voice tone',
  speaking_pace: 'Speaking pace',
  body_language: 'Body language',
  script_accuracy: 'Script accuracy'
} as const

export default function SessionResults({
  result
}: {
  result: SessionScoreMessage
}): React.JSX.Element {
  return (
    <section className="ph-results" aria-labelledby="ph-results-heading" aria-live="polite">
      <div className="ph-card-head">
        <h2 id="ph-results-heading">Practice results</h2>
        {result.score !== null && <strong>{result.score} / {result.max_score}</strong>}
      </div>
      {result.score === null ? (
        <p>No score is available. Scoring requires an enabled check with enough practice data.</p>
      ) : (
        <>
          <dl className="ph-results-breakdown">
            {Object.entries(result.categories).map(([category, breakdown]) => {
              const pace = breakdown.checks.moderate_pace
              return (
                <div key={category}>
                  <dt>{CATEGORY_LABELS[category as keyof typeof CATEGORY_LABELS]}</dt>
                  <dd>
                    {breakdown.points === null
                      ? 'Not measured'
                      : `${breakdown.points} / ${breakdown.max_points}`}
                    {pace?.average_wpm !== undefined && (
                      <small> · {pace.average_wpm} WPM (target {pace.target_wpm} WPM)</small>
                    )}
                  </dd>
                </div>
              )
            })}
          </dl>
          <p>Score based on {result.measured_points} measured rubric points.</p>
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
