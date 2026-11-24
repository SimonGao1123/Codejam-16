import type { Preferences } from './ruleDefinitions'
import { skeletonParts, statParts } from './webcamParts'

type Display = Pick<Preferences, 'showSkeleton' | 'showStats' | 'skeletonParts' | 'statParts'>
type Props = Display & { onChange: (change: Partial<Display>) => void }

/** Whether to draw the skeleton and the stats, and (advanced) which parts of each. */
export default function WebcamDisplay({
  showSkeleton,
  showStats,
  skeletonParts: skeletonShown,
  statParts: statsShown,
  onChange
}: Props): React.JSX.Element {
  return (
    <section className="la-card" aria-labelledby="la-webcam">
      <h2 id="la-webcam">Webcam display</h2>
      <div className="la-rules">
        <label className="la-rule la-toggle">
          <span className="la-rule-text">
            <span className="la-rule-name">Body skeleton</span>
            <span className="la-rule-meta">Draw the tracked body and hands</span>
          </span>
          <input
            type="checkbox"
            role="switch"
            checked={showSkeleton}
            onChange={(event) => onChange({ showSkeleton: event.target.checked })}
          />
        </label>
        <label className="la-rule la-toggle">
          <span className="la-rule-text">
            <span className="la-rule-name">Live stats</span>
            <span className="la-rule-meta">WPM, posture, hands and eye level</span>
          </span>
          <input
            type="checkbox"
            role="switch"
            checked={showStats}
            onChange={(event) => onChange({ showStats: event.target.checked })}
          />
        </label>
      </div>
      <details className="la-advanced">
        <summary>Advanced settings</summary>
        <PartPicker
          title="Skeleton parts"
          hint="Turn on Body skeleton to choose its parts."
          parts={skeletonParts}
          shown={skeletonShown}
          enabled={showSkeleton}
          onChange={(shown) => onChange({ skeletonParts: shown })}
        />
        <PartPicker
          title="Stats"
          hint="Turn on Live stats to choose which to show."
          parts={statParts}
          shown={statsShown}
          enabled={showStats}
          onChange={(shown) => onChange({ statParts: shown })}
        />
      </details>
    </section>
  )
}

function PartPicker<Id extends string>({
  title,
  hint,
  parts,
  shown,
  enabled,
  onChange
}: {
  title: string
  hint: string
  parts: readonly { id: Id; label: string }[]
  shown: Record<Id, boolean>
  enabled: boolean
  onChange: (shown: Record<Id, boolean>) => void
}): React.JSX.Element {
  return (
    <fieldset className="la-parts" disabled={!enabled}>
      <legend>{title}</legend>
      {!enabled && <p>{hint}</p>}
      {parts.map(({ id, label }) => (
        <label key={id}>
          <input
            type="checkbox"
            checked={shown[id]}
            onChange={(event) => onChange({ ...shown, [id]: event.target.checked })}
          />
          {label}
        </label>
      ))}
    </fieldset>
  )
}
