import type { SessionScoreMessage } from '../audio/audioProtocol'

const CATEGORY_LABELS = {
  filler: 'Filler words',
  voice_tone: 'Voice tone',
  body_language: 'Body language'
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
        <p>No audio or webcam data was available to score this session.</p>
      ) : (
        <>
          <dl className="ph-results-breakdown">
            {Object.entries(result.categories).map(([category, breakdown]) => (
              <div key={category}>
                <dt>{CATEGORY_LABELS[category as keyof typeof CATEGORY_LABELS]}</dt>
                <dd>
                  {breakdown.points === null
                    ? 'Not measured'
                    : `${breakdown.points} / ${breakdown.max_points}`}
                </dd>
              </div>
            ))}
          </dl>
          {result.measured_points < result.max_score && (
            <p>Score based on {result.measured_points} of {result.max_score} possible rubric points.</p>
          )}
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
