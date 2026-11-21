import { useEffect, useRef, useState } from 'react'
import type { PlaybackCheckpoint } from '../audio/audioProtocol'

type Props = {
  url: string
  visible: boolean
  recordedDuration: number
  checkpoints: PlaybackCheckpoint[]
}

function clock(seconds: number): string {
  const whole = Math.floor(seconds)
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

export default function PresentationPlayback({
  url,
  visible,
  recordedDuration,
  checkpoints
}: Props): React.JSX.Element {
  const playerRef = useRef<HTMLElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const [position, setPosition] = useState(0)
  const [mediaDuration, setMediaDuration] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(false)
  const [volume, setVolume] = useState(1)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!visible) videoRef.current?.pause()
  }, [visible])
  // Streamed WebM files may not carry duration metadata.
  const duration =
    Number.isFinite(mediaDuration) && mediaDuration > 0 ? mediaDuration : recordedDuration
  const moments = checkpoints.filter(({ at }) => at >= 0 && at <= duration)
  const markers = new Map<number, PlaybackCheckpoint[]>()
  for (const checkpoint of moments) {
    const slot = Math.floor((checkpoint.at / duration) * 40)
    const group = markers.get(slot)
    if (group) group.push(checkpoint)
    else markers.set(slot, [checkpoint])
  }

  function seek(at: number): void {
    const video = videoRef.current
    if (!video) return
    video.currentTime = at
    setPosition(at)
  }

  async function togglePlayback(): Promise<void> {
    const video = videoRef.current
    if (!video) return
    if (!video.paused) {
      video.pause()
      return
    }
    try {
      await video.play()
    } catch {
      setError('Could not play the recording.')
    }
  }

  return (
    <section ref={playerRef} className="ph-playback" aria-labelledby="ph-playback-heading">
      <div className="ph-card-head">
        <h2 id="ph-playback-heading">Presentation playback</h2>
      </div>
      <video
        ref={videoRef}
        src={url}
        playsInline
        preload="metadata"
        onDurationChange={(event) => setMediaDuration(event.currentTarget.duration)}
        onTimeUpdate={(event) => setPosition(event.currentTarget.currentTime)}
        onPlay={() => {
          setPlaying(true)
          setError('')
        }}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onError={() => setError('Could not load the recording.')}
        onVolumeChange={(event) => {
          setMuted(event.currentTarget.muted)
          setVolume(event.currentTarget.volume)
        }}
      />
      <div className="ph-playback-controls">
        <div className="ph-playback-timeline">
          <div className="ph-playback-markers" role="group" aria-label="Mistake markers">
            {[...markers].map(([slot, group]) => {
              const first = group[0]
              const label = group.map((checkpoint) => checkpoint.label).join(', ')
              return (
                <button
                  type="button"
                  key={slot}
                  className="ph-playback-marker"
                  style={{ left: `${(first.at / duration) * 100}%` }}
                  title={`${clock(first.at)} · ${label}`}
                  aria-label={`Jump to ${clock(first.at)}. ${label}`}
                  onClick={() => seek(first.at)}
                />
              )
            })}
          </div>
          <input
            type="range"
            aria-label="Playback position"
            aria-valuetext={`${clock(position)} of ${clock(duration)}`}
            min={0}
            max={duration}
            step="any"
            value={Math.min(position, duration)}
            disabled={duration <= 0}
            onChange={(event) => seek(event.currentTarget.valueAsNumber)}
          />
        </div>
        <div className="ph-playback-buttons">
          <button type="button" onClick={() => void togglePlayback()}>
            {playing ? 'Pause' : 'Play'}
          </button>
          <span>{clock(position)} / {clock(duration)}</span>
          <button
            type="button"
            onClick={() => {
              const video = videoRef.current
              if (video) video.muted = !video.muted
            }}
          >
            {muted ? 'Unmute' : 'Mute'}
          </button>
          <input
            type="range"
            aria-label="Playback volume"
            min={0}
            max={1}
            step={0.05}
            value={volume}
            onChange={(event) => {
              const video = videoRef.current
              if (video) {
                video.volume = event.currentTarget.valueAsNumber
                video.muted = false
              }
            }}
          />
          <button
            type="button"
            onClick={() => {
              const fullscreen = document.fullscreenElement
                ? document.exitFullscreen()
                : playerRef.current?.requestFullscreen()
              void fullscreen?.catch(console.error)
            }}
          >
            Fullscreen
          </button>
        </div>
        {error && <p role="status">{error}</p>}
        {moments.length > 0 && (
          <details className="ph-playback-checkpoints">
            <summary>Mistake checkpoints ({moments.length})</summary>
            <ul>
              {moments.map(({ at, category, label }, index) => (
                <li key={`${at}:${category}:${index}`}>
                  <button type="button" onClick={() => seek(at)}>
                    {clock(at)} · {label}
                  </button>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </section>
  )
}
