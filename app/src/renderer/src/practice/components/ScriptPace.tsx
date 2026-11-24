// The time the user has (minutes and seconds) and the pace it leads to, under the script box.
// Shared by the practice and upload pages.

import { useState } from 'react'

import { useAlertPreferences } from '../../liveAlert/useAlertPreferences'

type Props = {
  targetSeconds: number | null // the time the user has to present; null = not set
  setTargetSeconds: (seconds: number | null) => void
  disabled: boolean
  scriptPace: string | null // shown when the pace comes from the script and time
}

const wholeNumber = (text: string): number => Math.max(0, Math.floor(Number(text) || 0))

export default function ScriptPace({
  targetSeconds,
  setTargetSeconds,
  disabled,
  scriptPace
}: Props): React.JSX.Element {
  const [preferences] = useAlertPreferences()
  const sliderPace = preferences.rules.pace?.threshold ?? 140
  // kept as typed, so a field can be empty or 0 while the other is filled in
  const [minutes, setMinutes] = useState(targetSeconds ? `${Math.floor(targetSeconds / 60)}` : '')
  const [seconds, setSeconds] = useState(targetSeconds ? `${targetSeconds % 60}` : '')

  function update(newMinutes: string, newSeconds: string): void {
    setMinutes(newMinutes)
    setSeconds(newSeconds)
    setTargetSeconds(wholeNumber(newMinutes) * 60 + wholeNumber(newSeconds) || null)
  }

  /** On leaving a field: 90 seconds becomes 1 min 30 s, and decimals and minus signs are dropped.
   *  An empty field stays empty. */
  function tidy(): void {
    const total = wholeNumber(minutes) * 60 + wholeNumber(seconds)
    update(
      minutes || total >= 60 ? `${Math.floor(total / 60)}` : '',
      seconds ? `${total % 60}` : ''
    )
  }

  return (
    <div className="ph-script-pace">
      <div className="ph-script-time" role="group" aria-label="Time you have">
        <span>Time you have</span>
        <label>
          <input
            type="number"
            min={0}
            step={1}
            inputMode="numeric"
            value={minutes}
            onChange={(e) => update(e.target.value, seconds)}
            onBlur={tidy}
            disabled={disabled}
            placeholder="–"
            aria-label="Minutes"
          />
          min
        </label>
        <label>
          <input
            type="number"
            min={0}
            step={1}
            inputMode="numeric"
            value={seconds}
            onChange={(e) => update(minutes, e.target.value)}
            onBlur={tidy}
            disabled={disabled}
            placeholder="–"
            aria-label="Seconds"
          />
          sec
        </label>
      </div>
      <span>
        {scriptPace ??
          (targetSeconds
            ? `Add a script to set your pace from your time. Until then: ${sliderPace} WPM (target pace in Settings)`
            : `Desired pace: ${sliderPace} WPM (target pace in Settings). Enter your time and a script to pace yourself to finish on time.`)}
      </span>
    </div>
  )
}
