import { useEffect, useState } from 'react'
import type { PracticeFolder, PracticeLibrary } from '../../../main/practiceLibrary'
import FolderSelect from '../menu/FolderSelect'
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

export default function Practice({
  visible,
  onBusyChange,
  folders,
  initialFolderId,
  onFolderChange,
  onLibraryChange
}: {
  visible: boolean
  onBusyChange: (busy: boolean) => void
  folders: PracticeFolder[]
  initialFolderId: string | null
  onFolderChange: (folderId: string | null) => void
  onLibraryChange: (library: PracticeLibrary) => void
}): React.JSX.Element {
  const [calibrated, setCalibrated] = useState(false)
  const session = usePracticeSession(calibrated)
  const [score, setScore] = useState<SessionScoreMessage | null>(null)
  const [folderId, setFolderId] = useState(initialFolderId)
  const [finishing, setFinishing] = useState(false)
  const [scoreUnavailable, setScoreUnavailable] = useState(false)
  const [libraryError, setLibraryError] = useState(false)
  const { running, finished, camera, microphone, cameraStream, micStream, pending } = session
  const [script, setScript] = useState('') // optional; empty = no script tracking
  const [targetMinutes, setTargetMinutes] = useState<number | null>(null) // time the user has
  const speech = useAudioAnalysis(micStream, script, targetMinutes, session.startedAt)
  const busy = running || session.preparing || session.saving || finishing
  useEffect(() => onBusyChange(busy), [busy, onBusyChange])

  async function endPractice(): Promise<void> {
    if (!running || finishing) return
    setFinishing(true)
    const completedAt = Date.now()
    try {
      const [recording, result] = await Promise.all([session.endPractice(), speech.finishScoring()])
      setScore(result)
      setScoreUnavailable(result === null)
      const savedScore =
        result?.score != null
          ? { points: result.score, maximum: result.max_score, measured: result.measured_points }
          : null
      if (recording || savedScore) {
        const library = await window.api.practiceLibrary.saveAttempt({
          id: recording?.id ?? crypto.randomUUID(),
          folderId,
          completedAt,
          score: savedScore
        })
        onLibraryChange(library)
      }
    } catch (error) {
      console.error(error)
      setLibraryError(true)
    } finally {
      setFinishing(false)
    }
  }

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
      <div className="ph-practice-folder">
        <FolderSelect
          folders={folders}
          value={folderId}
          label="Practice folder"
          onChange={(id) => {
            setFolderId(id)
            onFolderChange(id)
          }}
          disabled={busy}
        />
      </div>
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
          finishing={finishing}
          finished={finished}
          seconds={session.seconds}
          camera={camera}
          microphone={microphone}
          deviceError={session.deviceError}
          setCamera={session.setCamera}
          setMicrophone={session.setMicrophone}
          startPractice={() => {
            setScore(null)
            setScoreUnavailable(false)
            setLibraryError(false)
            return session.startPractice()
          }}
          endPractice={() => void endPractice()}
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
          visible={visible}
          recordedDuration={session.recordingDuration}
          checkpoints={score?.checkpoints ?? []}
        />
      )}
      {finished && score && <SessionResults result={score} />}
      {scoreUnavailable && <p role="status">A score wasn’t available for this attempt.</p>}
      {libraryError && (
        <p className="ph-error" role="status">
          Couldn’t save this attempt to the folder.
        </p>
      )}
      <TranscriptPanel speech={speech} running={running} />
    </main>
  )
}
