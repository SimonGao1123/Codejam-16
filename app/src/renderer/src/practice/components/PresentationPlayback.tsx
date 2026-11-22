import { useRef, useState } from 'react'
import type { PlaybackCheckpoint } from '../audio/audioProtocol'

type Props = {
  url: string
  recordedDuration: number
  checkpoints: PlaybackCheckpoint[]
  isVideo?: boolean
}

function clock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00'
  const whole = Math.floor(seconds)
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

export default function PresentationPlayback({
  url,
  recordedDuration,
  checkpoints,
  isVideo = true
}: Props): React.JSX.Element {
  const playerRef = useRef<HTMLElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const [position, setPosition] = useState(0)
  const [mediaDuration, setMediaDuration] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(false)
  const [volume, setVolume] = useState(1)
  const [error, setError] = useState('')

  // Streamed WebM files or unprobed files may have non-finite metadata.
  const rawDuration =
    Number.isFinite(mediaDuration) && mediaDuration > 0
      ? mediaDuration
      : Number.isFinite(recordedDuration) && recordedDuration > 0
        ? recordedDuration
        : 0
  const duration = Math.max(0, rawDuration)

  const moments =
    duration > 0
      ? checkpoints.filter(({ at }) => Number.isFinite(at) && at >= 0 && at <= duration)
      : []
  const markers = new Map<number, PlaybackCheckpoint[]>()
  if (duration > 0) {
    for (const checkpoint of moments) {
      const slot = Math.floor((checkpoint.at / duration) * 40)
      const group = markers.get(slot)
      if (group) group.push(checkpoint)
      else markers.set(slot, [checkpoint])
    }
  }

  function seek(at: number): void {
    const video = videoRef.current
    if (!video) return
    if (!Number.isFinite(at)) return
    const maxDur = duration > 0 ? duration : Number.isFinite(video.duration) ? video.duration : at
    const target = Math.max(0, Math.min(at, maxDur > 0 ? maxDur : at))
    video.currentTime = target
    setPosition(target)
  }

  async function togglePlayback(): Promise<void> {
    const video = videoRef.current
    if (!video) return
    if (!video.paused) {
      video.pause()
      return
    }
    // Loop back to beginning if playback finished or near the end
    if (video.ended || (duration > 0 && video.currentTime >= duration - 0.2)) {
      video.currentTime = 0
      setPosition(0)
    }
    try {
      await video.play()
      setError('')
    } catch (err) {
      console.warn('Playback error:', err)
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
        controls
        preload="auto"
        style={{
          cursor: 'pointer',
          ...(isVideo === false ? { height: '56px' } : {})
        }}
        onClick={(): void => void togglePlayback()}
        onLoadedMetadata={(event): void => {
          const d = event.currentTarget.duration
          if (Number.isFinite(d) && d > 0) setMediaDuration(d)
        }}
        onDurationChange={(event): void => {
          const d = event.currentTarget.duration
          if (Number.isFinite(d) && d > 0) setMediaDuration(d)
        }}
        onTimeUpdate={(event): void => setPosition(event.currentTarget.currentTime)}
        onPlay={(): void => {
          setPlaying(true)
          setError('')
        }}
        onPause={(): void => setPlaying(false)}
        onEnded={(): void => {
          setPlaying(false)
          if (duration > 0) setPosition(duration)
        }}
        onError={(): void => setError('Could not load the recording.')}
        onVolumeChange={(event): void => {
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
              const pct = duration > 0 ? (first.at / duration) * 100 : 0
              return (
                <button
                  type="button"
                  key={slot}
                  className="ph-playback-marker"
                  style={{ left: `${Math.min(100, Math.max(0, pct))}%` }}
                  title={`${clock(first.at)} · ${label}`}
                  aria-label={`Jump to ${clock(first.at)}. ${label}`}
                  onClick={(): void => seek(first.at)}
                />
              )
            })}
          </div>
          <input
            type="range"
            aria-label="Playback position"
            aria-valuetext={`${clock(position)} of ${clock(duration)}`}
            min={0}
            max={duration > 0 ? duration : 100}
            step="any"
            value={duration > 0 ? Math.min(position, duration) : position}
            disabled={duration <= 0}
            onChange={(event): void => seek(event.currentTarget.valueAsNumber)}
          />
        </div>
        <div className="ph-playback-buttons">
          <button type="button" onClick={(): void => void togglePlayback()}>
            {playing ? 'Pause' : 'Play'}
          </button>
          <span>
            {clock(position)} / {clock(duration)}
          </span>
          <button
            type="button"
            onClick={(): void => {
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
            onChange={(event): void => {
              const video = videoRef.current
              if (video) {
                video.volume = event.currentTarget.valueAsNumber
                video.muted = false
              }
            }}
          />
          <button
            type="button"
            onClick={(): void => {
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
                  <button type="button" onClick={(): void => seek(at)}>
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
