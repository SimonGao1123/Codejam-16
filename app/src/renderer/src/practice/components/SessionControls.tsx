function clock(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  return [minutes, seconds % 60].map((value) => String(value).padStart(2, '0')).join(':')
}

type Props = {
  running: boolean
  preparing: boolean
  countdown: number | null
  saving: boolean
  finishing: boolean
  finished: boolean
  seconds: number
  camera: boolean
  microphone: boolean
  deviceError: string
  setCamera: (enabled: boolean) => void
  setMicrophone: (enabled: boolean) => void
  startPractice: () => Promise<void>
  endPractice: () => void
  waitingForCalibration: boolean // countdown done, waiting for the camera calibration
  cancelPractice: () => void
}

export default function SessionControls({
  running,
  preparing,
  countdown,
  saving,
  finishing,
  finished,
  seconds,
  camera,
  microphone,
  deviceError,
  setCamera,
  setMicrophone,
  startPractice,
  endPractice,
  waitingForCalibration,
  cancelPractice
}: Props): React.JSX.Element {
  let actionLabel = finished ? 'Start another session' : 'Start practice'
  if (running) actionLabel = 'Stop presenting'
  if (preparing) actionLabel = 'Getting ready…'
  if (waitingForCalibration) actionLabel = 'Cancel'
  if (finishing) actionLabel = 'Finishing practice…'
  if (saving) actionLabel = 'Saving recording…'

  return (
    <aside className="ph-controls" aria-labelledby="ph-session-heading">
      <h2 id="ph-session-heading">Session</h2>
      <div
        className="ph-time"
        aria-label={countdown === null ? `Elapsed time ${clock(seconds)}` : undefined}
      >
        {countdown === null ? clock(seconds) : countdown}
      </div>
      <p className="ph-time-label">
        {waitingForCalibration
          ? 'Calibrating: the timer starts once you’re in frame and still'
          : preparing
            ? 'Get ready'
            : finished
              ? 'Session duration'
              : 'Elapsed time'}
      </p>
      <fieldset disabled={running || preparing || saving || finishing}>
        <legend>Devices</legend>
        <label>
          <input
            type="checkbox"
            checked={camera}
            onChange={(event) => setCamera(event.target.checked)}
          />{' '}
          Camera
        </label>
        <label>
          <input
            type="checkbox"
            checked={microphone}
            onChange={(event) => setMicrophone(event.target.checked)}
          />{' '}
          Microphone
        </label>
      </fieldset>
      {deviceError && (
        <p className="ph-error" role="status">
          {deviceError}
        </p>
      )}
      <button
        className="ph-session-action"
        data-active={running}
        disabled={(preparing && !waitingForCalibration) || saving || finishing}
        onClick={
          running
            ? endPractice
            : waitingForCalibration
              ? cancelPractice
              : () => void startPractice()
        }
      >
        {actionLabel}
      </button>
    </aside>
  )
}
