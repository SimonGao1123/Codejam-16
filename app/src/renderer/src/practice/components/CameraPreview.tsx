import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { ClientMessage } from '../camera/webcamProtocol'

import WebcamControls from './WebcamControls'
import WebcamView, { type WebcamHandle, type WebcamStatus } from './WebcamView'

type Props = {
  cameraStream: MediaStream | null
  camera: boolean
  pending: boolean
  running: boolean
  logWebcam: (message: ClientMessage) => void
  children: ReactNode
}

export default function CameraPreview({
  cameraStream,
  camera,
  pending,
  running,
  logWebcam,
  children
}: Props): React.JSX.Element {
  const webcam = useRef<WebcamHandle>(null)
  useEffect(() => {
    const view = webcam.current
    view?.setLogger(logWebcam)
    return () => view?.setLogger(null)
  }, [logWebcam])
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
      <WebcamControls webcam={webcam} status={status} />
      {children}
    </section>
  )
}
