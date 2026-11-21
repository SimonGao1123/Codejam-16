import { useState } from 'react'
import type { SessionScoreMessage } from './audio/audioProtocol'
import { useAudioAnalysis } from './audio/useAudioAnalysis'
import CameraPreview from './components/CameraPreview'
import MicrophoneMeter from './components/MicrophoneMeter'
import PresentationPlayback from './components/PresentationPlayback'
import ScriptPanel from './components/ScriptPanel'
import SessionControls from './components/SessionControls'
import SessionResults from './components/SessionResults'
import TranscriptPanel from './components/TranscriptPanel'
import { usePracticeSession } from './usePracticeSession'

export default function Practice(): React.JSX.Element {
  // this session's posture calibration has finished; with the camera on, the session starts after it
  const [calibrated, setCalibrated] = useState(false)
  const session = usePracticeSession(calibrated)
  const [score, setScore] = useState<SessionScoreMessage | null>(null)
  const { running, finished, camera, microphone, cameraStream, micStream, pending } = session
  const [script, setScript] = useState('') // optional; empty = no script tracking
  const [targetMinutes, setTargetMinutes] = useState<number | null>(null) // time the user has
  const speech = useAudioAnalysis(micStream, script, targetMinutes, session.startedAt, setScore)

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
          running={running || session.preparing}
          logWebcam={session.logWebcam}
          wpm={speech.wpm}
          wpmUpdatedAt={speech.wpmUpdatedAt}
          avgWpm={speech.avgWpm}
          desiredWpm={speech.desiredWpm}
          onCalibrated={setCalibrated}
        >
          <MicrophoneMeter micStream={micStream} pending={pending && microphone} />
        </CameraPreview>
        <SessionControls
          running={running}
          preparing={session.preparing}
          countdown={session.countdown}
          saving={session.saving}
          finished={finished}
          seconds={session.seconds}
          camera={camera}
          microphone={microphone}
          deviceError={session.deviceError}
          setCamera={session.setCamera}
          setMicrophone={session.setMicrophone}
          startPractice={() => {
            setScore(null)
            return session.startPractice()
          }}
          endPractice={session.endPractice}
          waitingForCalibration={session.waitingForCalibration}
          cancelPractice={session.cancelPractice}
        />
      </div>
      <ScriptPanel
        script={script}
        setScript={setScript}
        running={running}
        speech={speech}
        targetMinutes={targetMinutes}
        setTargetMinutes={setTargetMinutes}
      />
      {session.recordingError && (
        <p className="ph-error" role="status">
          {session.recordingError}
        </p>
      )}
      {session.playbackUrl && (
        <PresentationPlayback
          url={session.playbackUrl}
          recordedDuration={session.recordingDuration}
          checkpoints={score?.checkpoints ?? []}
        />
      )}
      {finished && score && <SessionResults result={score} />}
      <TranscriptPanel speech={speech} running={running} />
    </main>
  )
}
