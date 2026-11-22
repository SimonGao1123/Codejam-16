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
  voiceCalibrated: boolean // true once the user has saved a voice calibration
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
  cancelPractice,
  voiceCalibrated
}: Props): React.JSX.Element {
  // Only the "start" states (Start practice / Start another session) need a voice calibration.
  // "Stop presenting" and "Cancel" must keep working.
  const blockedByVoice = !running && !waitingForCalibration && !voiceCalibrated

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
      {blockedByVoice && (
        <p id="ph-voice-hint" className="ph-hint" role="status">
          Calibrate your voice first to start a session.
        </p>
      )}
      <button
        className="ph-session-action"
        data-active={running}
        disabled={(preparing && !waitingForCalibration) || saving || finishing || blockedByVoice}
        aria-describedby={blockedByVoice ? 'ph-voice-hint' : undefined}
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