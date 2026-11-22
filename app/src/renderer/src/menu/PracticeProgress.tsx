import type { PracticeAttempt } from '../../../main/practiceLibrary'
import { max1, score1 } from '../practice/scoreFormat'

const shortDate = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
const fullDate = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
const RECENT_ATTEMPTS = 8

export default function PracticeProgress({
  attempts
}: {
  attempts: PracticeAttempt[]
}): React.JSX.Element {
  const recent = attempts
    .flatMap(({ score, completedAt, id }) =>
      score && score.maximum > 0
        ? [{ score, completedAt, id, percent: (100 * score.points) / score.maximum }]
        : []
    )
    .slice(0, RECENT_ATTEMPTS)
    .reverse()

  return (
    <section className="ph-menu-card ph-menu-progress" aria-labelledby="ph-progress-heading">
      <div className="ph-card-head">
        <h2 id="ph-progress-heading">Practice progress</h2>
        {recent.length > 0 && (
          <span>
            Last {recent.length} scored {recent.length === 1 ? 'attempt' : 'attempts'}
          </span>
        )}
      </div>
      {recent.length === 0 ? (
        <p>Complete a scored practice session in this folder to start tracking progress.</p>
      ) : (
        <div className="ph-progress-chart">
          <div className="ph-progress-axis" aria-hidden="true">
            <span>100%</span>
            <span>50%</span>
            <span>0%</span>
          </div>
          <ol aria-label="Practice scores, oldest to newest">
            {recent.map(({ id, score, completedAt, percent }) => (
              <li
                key={id}
                title={`${fullDate.format(completedAt)} · ${score1(score.points)}/${max1(score.maximum)} · ${max1(score.measured)} rubric points measured`}
              >
                <div className="ph-progress-track">
                  <div className="ph-progress-bar" style={{ height: `${percent}%` }}>
                    <span>{score1(percent)}%</span>
                  </div>
                </div>
                <time dateTime={new Date(completedAt).toISOString()}>
                  {shortDate.format(completedAt)}
                </time>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  )
}
