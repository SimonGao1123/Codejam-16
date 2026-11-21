function clock(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  return [minutes, seconds % 60].map((value) => String(value).padStart(2, '0')).join(':')
}

type Props = {
  running: boolean
  finished: boolean
  seconds: number
  camera: boolean
  microphone: boolean
  deviceError: string
  setCamera: (enabled: boolean) => void
  setMicrophone: (enabled: boolean) => void
  startPractice: () => Promise<void>
  endPractice: () => void
}

export default function SessionControls({
  running,
  finished,
  seconds,
  camera,
  microphone,
  deviceError,
  setCamera,
  setMicrophone,
  startPractice,
  endPractice
}: Props): React.JSX.Element {
  return (
    <aside className="ph-controls" aria-labelledby="ph-session-heading">
      <h2 id="ph-session-heading">Session</h2>
      <div className="ph-time" aria-label={`Elapsed time ${clock(seconds)}`}>
        {clock(seconds)}
      </div>
      <p className="ph-time-label">{finished ? 'Session duration' : 'Elapsed time'}</p>
      <fieldset disabled={running}>
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
        onClick={running ? endPractice : () => void startPractice()}
      >
        {running ? 'End practice' : finished ? 'Start another session' : 'Start practice'}
      </button>
    </aside>
  )
}
