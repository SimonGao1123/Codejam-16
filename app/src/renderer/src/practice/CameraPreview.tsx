import { useEffect, useRef, type ReactNode } from 'react'

type Props = {
  cameraStream: MediaStream | null
  camera: boolean
  pending: boolean
  running: boolean
  children: ReactNode
}

export default function CameraPreview({
  cameraStream,
  camera,
  pending,
  running,
  children
}: Props): React.JSX.Element {
  const video = useRef<HTMLVideoElement>(null)
  useEffect(() => {
    if (video.current) video.current.srcObject = cameraStream
  }, [cameraStream])

  return (
    <section className="ph-stage" aria-labelledby="ph-camera-heading">
      <div className="ph-card-head">
        <h2 id="ph-camera-heading">Camera preview</h2>
        <span>{cameraStream ? 'Camera on' : pending && camera ? 'Connecting' : 'Camera off'}</span>
      </div>
      <div className="ph-video">
        <video
          ref={video}
          autoPlay
          playsInline
          muted
          hidden={!cameraStream}
          aria-label="Live camera preview"
        />
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
      {children}
    </section>
  )
}
