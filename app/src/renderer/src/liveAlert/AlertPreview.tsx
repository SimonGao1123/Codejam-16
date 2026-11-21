import { styles, type Style } from './ruleDefinitions'

type Props = {
  style: Style
  enabled: boolean
  preview: boolean
  cue: string
  onStyleChange: (style: Style) => void
  onPreview: () => void
}

export default function AlertPreview({
  style,
  enabled,
  preview,
  cue,
  onStyleChange,
  onPreview
}: Props): React.JSX.Element {
  return (
    <section className="la-card la-display" aria-labelledby="la-display">
      <h2 id="la-display">Alert appearance</h2>
      <div className="la-styles" role="group" aria-label="Alert style">
        {styles.map((option) => (
          <button
            key={option.id}
            aria-pressed={style === option.id}
            onClick={() => onStyleChange(option.id)}
          >
            {option.label}
          </button>
        ))}
      </div>
      <div className="la-stage" aria-live="polite">
        {preview && enabled && style !== 'silent' ? (
          <div className={`la-cue la-cue-${style}`}>{cue}</div>
        ) : (
          <p>
            {!enabled
              ? 'This rule is disabled.'
              : style === 'silent'
                ? 'Silent mode hides on-screen alerts.'
                : 'Preview a sample alert.'}
          </p>
        )}
      </div>
      <button
        className="la-preview"
        disabled={!enabled || style === 'silent' || preview}
        onClick={onPreview}
      >
        Preview alert
      </button>
    </section>
  )
}
