// Rolling pitch or volume graph, drawn from the last 15 seconds of live voice readings.

import { useEffect, useRef } from 'react'

export interface VoiceSample {
  t: number // seconds (performance.now() / 1000)
  value: number | null // null breaks the trace during an unvoiced pitch sample
}

type Props = {
  samples: React.RefObject<VoiceSample[]>
  metric: 'pitch' | 'volume' | 'wpm'
  typicalHz?: number | null
  spikeHz?: number | null
  typicalDbfs?: number | null
  quietDbfs?: number | null
  silenceDbfs?: number | null // volume graph: below this the speaker counts as not speaking
  desiredWpm?: number | null // pace graph: the pace to aim for
  baselineLabel?: string
  live: boolean // false: freeze on the last reading (session ended)
}

export const VOICE_WINDOW_S = 15
const SCALES = {
  pitch: { ticks: [50, 100, 200, 300, 400, 500], unit: 'Hz' },
  volume: { ticks: [-80, -60, -40, -20, 0], unit: 'dBFS' },
  wpm: { ticks: [0, 80, 120, 160, 200, 240], unit: 'WPM' }
}
const PITCH_JOIN_S = 0.251
const VOLUME_JOIN_S = 0.25
const COLORS = {
  grid: '#38334e',
  label: '#8f89a8',
  trace: '#c4aeff',
  typical: '#8fd6a1',
  spike: '#f08a8a',
  silence: '#8f89a8'
}
const FONT = '12px -apple-system, "Segoe UI", sans-serif'

export default function VoiceGraph({
  samples,
  metric,
  typicalHz = null,
  spikeHz = null,
  typicalDbfs = null,
  quietDbfs = null,
  silenceDbfs = null,
  desiredWpm = null,
  baselineLabel = 'Calibrated',
  live
}: Props): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const linesRef = useRef({
    typicalHz,
    spikeHz,
    typicalDbfs,
    quietDbfs,
    silenceDbfs,
    desiredWpm,
    live
  })

  useEffect(() => {
    linesRef.current = { typicalHz, spikeHz, typicalDbfs, quietDbfs, silenceDbfs, desiredWpm, live }
  }, [typicalHz, spikeHz, typicalDbfs, quietDbfs, silenceDbfs, desiredWpm, live])

  useEffect(() => {
    const canvas = canvasRef.current!
    let frame = 0
    function draw(): void {
      frame = requestAnimationFrame(draw)
      // match the canvas to its on-screen size (sharp on high-DPI screens)
      const dpr = window.devicePixelRatio || 1
      const cssW = canvas.clientWidth
      const cssH = canvas.clientHeight
      if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
        canvas.width = Math.round(cssW * dpr)
        canvas.height = Math.round(cssH * dpr)
      }
      const ctx = canvas.getContext('2d')!
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, cssW, cssH)
      render(ctx, cssW, cssH, samples.current, linesRef.current, metric)
    }
    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [samples, metric])

  const lowHz = typicalHz && spikeHz ? typicalHz ** 2 / spikeHz : null
  return (
    <>
      <canvas
        ref={canvasRef}
        className="ph-pitch-graph"
        aria-label={`Voice ${metric} over the last ${VOICE_WINDOW_S} seconds`}
      />
      {metric === 'pitch' && typicalHz && spikeHz && lowHz && (
        <div className="ph-pitch-legend">
          <span>
            {baselineLabel} pitch {Math.round(typicalHz)} Hz
          </span>
          <span>
            Acceptable range {Math.round(lowHz)}–{Math.round(spikeHz)} Hz
          </span>
        </div>
      )}
      {metric === 'volume' && (typicalDbfs !== null || silenceDbfs !== null) && (
        <div className="ph-pitch-legend">
          {typicalDbfs !== null && (
            <span>
              {baselineLabel} volume {Math.round(typicalDbfs)} dBFS
            </span>
          )}
          {quietDbfs !== null && <span>Quiet speech threshold {Math.round(quietDbfs)} dBFS</span>}
          {silenceDbfs !== null && <span>Speech cutoff {Math.round(silenceDbfs)} dBFS</span>}
        </div>
      )}
    </>
  )
}

function render(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  samples: VoiceSample[],
  lines: {
    typicalHz: number | null
    spikeHz: number | null
    typicalDbfs: number | null
    quietDbfs: number | null
    silenceDbfs: number | null
    desiredWpm: number | null
    live: boolean
  },
  metric: Props['metric']
): void {
  const { ticks, unit } = SCALES[metric]
  const left = 68
  const right = w - 12
  const top = 10
  const bottom = h - 22
  if (right <= left || bottom <= top) return

  const last = samples.at(-1)
  const end = metric === 'wpm' && lines.live ? performance.now() / 1000 : (last?.t ?? 0)
  // Show the full pace history. Scale short pitch and volume recordings to the available data.
  const windowSeconds =
    metric === 'wpm' && samples.length
      ? Math.max(VOICE_WINDOW_S, end - samples[0].t)
      : last
        ? Math.min(VOICE_WINDOW_S, Math.max(0.3, end - samples[0].t))
        : VOICE_WINDOW_S
  const start = end - windowSeconds
  const x = (t: number): number => left + ((t - start) / windowSeconds) * (right - left)
  const y = (value: number): number => {
    const v = Math.min(ticks.at(-1)!, Math.max(ticks[0], value))
    return bottom - ((v - ticks[0]) / (ticks.at(-1)! - ticks[0])) * (bottom - top)
  }

  ctx.font = FONT
  ctx.lineWidth = 1
  if (lines.spikeHz && lines.typicalHz) {
    const lowHz = lines.typicalHz ** 2 / lines.spikeHz
    ctx.fillStyle = 'rgba(143, 214, 161, 0.10)'
    ctx.fillRect(left, y(lines.spikeHz), right - left, y(lowHz) - y(lines.spikeHz))
  }
  // Horizontal grid and measurement labels.
  ctx.textAlign = 'right'
  ctx.textBaseline = 'middle'
  for (const tick of ticks) {
    ctx.strokeStyle = COLORS.grid
    ctx.beginPath()
    ctx.moveTo(left, y(tick))
    ctx.lineTo(right, y(tick))
    ctx.stroke()
    ctx.fillStyle = COLORS.label
    ctx.fillText(`${tick} ${unit}`, left - 8, y(tick))
  }
  // time axis: seconds ago
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  for (let tick = 0; tick <= 3; tick++) {
    const ago = (windowSeconds * tick) / 3
    const label = tick ? `-${Number(ago.toFixed(1))}s` : 'now'
    ctx.fillText(label, x(end - ago), bottom + 6)
  }

  // reference lines
  const dashed = (hz: number, color: string, label?: string): void => {
    ctx.strokeStyle = color
    ctx.setLineDash([6, 4])
    ctx.beginPath()
    ctx.moveTo(left, y(hz))
    ctx.lineTo(right, y(hz))
    ctx.stroke()
    ctx.setLineDash([])
    if (label) {
      ctx.fillStyle = color
      ctx.textAlign = 'right'
      ctx.textBaseline = 'bottom'
      ctx.fillText(label, right - 4, Math.max(y(hz) - 3, top + 12))
    }
  }
  if (lines.spikeHz && lines.typicalHz) {
    const lowHz = lines.typicalHz ** 2 / lines.spikeHz
    dashed(lines.spikeHz, COLORS.spike)
    dashed(lowHz, COLORS.spike)
  }
  if (lines.silenceDbfs !== null) dashed(lines.silenceDbfs, COLORS.silence)
  if (lines.typicalHz) dashed(lines.typicalHz, COLORS.typical)
  if (lines.typicalDbfs !== null) dashed(lines.typicalDbfs, COLORS.typical)
  if (lines.quietDbfs !== null) dashed(lines.quietDbfs, COLORS.spike)
  if (lines.desiredWpm)
    dashed(lines.desiredWpm, COLORS.typical, `Desired ${lines.desiredWpm.toFixed(0)} WPM`)

  // Keep short pitch gaps readable and leave longer periods without a reading blank.
  const joinSeconds = metric === 'pitch' ? PITCH_JOIN_S : VOLUME_JOIN_S
  ctx.save()
  ctx.beginPath()
  ctx.rect(left, top - 3, right - left, bottom - top + 6)
  ctx.clip()
  ctx.strokeStyle = COLORS.trace
  ctx.lineWidth = 2
  ctx.lineJoin = 'round'
  ctx.beginPath()
  const dots: { x: number; y: number }[] = []
  let prev: { t: number; x: number; y: number; value: number } | null = null
  let segmentHasLine = false
  for (const s of samples) {
    if (s.t < start - joinSeconds || s.value === null) continue
    const point = { t: s.t, x: x(s.t), y: y(s.value), value: s.value }
    const gap = prev ? s.t - prev.t : Infinity
    // Across missing pitch readings, bridge nearby frequencies without drawing a false jump.
    if (
      prev &&
      gap <= joinSeconds &&
      (metric !== 'pitch' || gap <= 0.075 || Math.abs(12 * Math.log2(s.value / prev.value)) <= 5)
    ) {
      ctx.lineTo(point.x, point.y)
      segmentHasLine = true
    } else {
      if (prev && !segmentHasLine && prev.t >= start) dots.push(prev)
      ctx.moveTo(point.x, point.y)
      segmentHasLine = false
    }
    prev = point
  }
  ctx.stroke()
  ctx.restore()
  // Draw isolated readings and the endpoint so they remain visible without a line.
  if (prev && prev.t >= start && (!segmentHasLine || prev.t === end)) dots.push(prev)
  if (dots.length) {
    ctx.fillStyle = COLORS.trace
    ctx.beginPath()
    for (const dot of dots) {
      ctx.moveTo(dot.x + 3, dot.y)
      ctx.arc(dot.x, dot.y, 3, 0, 2 * Math.PI)
    }
    ctx.fill()
  }
}
