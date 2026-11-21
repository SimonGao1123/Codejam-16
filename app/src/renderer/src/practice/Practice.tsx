import CameraPreview from './CameraPreview'
import MicrophoneMeter from './MicrophoneMeter'
import SessionControls from './SessionControls'
import { usePracticeSession } from './usePracticeSession'

export default function Practice(): React.JSX.Element {
  const session = usePracticeSession()
  const { running, finished, camera, microphone, cameraStream, micStream, pending } = session

  return (
    <main className="ph-practice">
      <header className="ph-heading">
        <div>
          <h1>Practice session</h1>
          <p>Rehearse your presentation with a camera preview and microphone level.</p>
        </div>
        <span className="ph-session-state" data-active={running}>
          {running ? 'In progress' : finished ? 'Session complete' : 'Ready to practice'}
        </span>
      </header>
      <div className="ph-workspace">
        <CameraPreview
          cameraStream={cameraStream}
          camera={camera}
          pending={pending}
          running={running}
        >
          <MicrophoneMeter micStream={micStream} pending={pending && microphone} />
        </CameraPreview>
        <SessionControls
          running={running}
          finished={finished}
          seconds={session.seconds}
          camera={camera}
          microphone={microphone}
          deviceError={session.deviceError}
          setCamera={session.setCamera}
          setMicrophone={session.setMicrophone}
          startPractice={session.startPractice}
          endPractice={session.endPractice}
        />
      </div>
    </main>
  )
}
