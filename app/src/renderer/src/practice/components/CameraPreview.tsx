import { useRef, useState, type ReactNode } from 'react'

import WebcamControls from './WebcamControls'
import WebcamView, { type WebcamHandle, type WebcamStatus } from './WebcamView'

type Props = {
  cameraStream: MediaStream | null
  micStream: MediaStream | null
  camera: boolean
  pending: boolean
  running: boolean
  children: ReactNode
}

export default function CameraPreview({
  cameraStream,
  micStream,
  camera,
  pending,
  running,
  children
}: Props): React.JSX.Element {
  const webcam = useRef<WebcamHandle>(null)
  const [status, setStatus] = useState<WebcamStatus>({
    ready: false,
    error: null,
    connected: false,
    calibration: null
  })

  return (
    <section className="ph-stage" aria-labelledby="ph-camera-heading">
      <div className="ph-card-head">
        <h2 id="ph-camera-heading">Camera preview</h2>
        <span>{cameraStream ? 'Camera on' : pending && camera ? 'Connecting' : 'Camera off'}</span>
      </div>
      <div className="ph-video">
        <WebcamView ref={webcam} stream={cameraStream} onStatus={setStatus} />
        {!cameraStream && (
          <div className="ph-video-empty">
            <span className="ph-camera-icon" aria-hidden="true" />
            <p>
              {camera && !running
                ? 'Your camera preview appears when you start.'
                : 'Camera preview is off.'}
            </p>
          </div>
        )}
      </div>
      <WebcamControls
        webcam={webcam}
        status={status}
        cameraStream={cameraStream}
        micStream={micStream}
      />
      {children}
    </section>
  )
}
