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
  calibrated?: boolean
  live: boolean // false: freeze on the last reading (session ended)
}

export const VOICE_WINDOW_S = 15
const SCALES = {
  pitch: { ticks: [50, 100, 200, 300, 400, 500], unit: 'Hz' },
  volume: { ticks: [-80, -60, -40, -20, 0], unit: 'dBFS' },
  wpm: { ticks: [0, 80, 120, 160, 200, 240], unit: 'WPM' }
}
const JOIN_S = 0.25 // readings further apart than this aren't joined (a pause in speech)
const COLORS = {
  grid: '#38334e',
  label: '#8f89a8',
  trace: '#c4aeff',
  typical: '#8fd6a1',
  spike: '#f08a8a'
}
const FONT = '12px -apple-system, "Segoe UI", sans-serif'

export default function VoiceGraph({
  samples,
  metric,
  typicalHz = null,
  spikeHz = null,
  calibrated = false,
  live
}: Props): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const linesRef = useRef({ typicalHz, spikeHz, calibrated, live })

  useEffect(() => {
    linesRef.current = { typicalHz, spikeHz, calibrated, live }
  }, [typicalHz, spikeHz, calibrated, live])

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

  return (
    <canvas
      ref={canvasRef}
      className="ph-pitch-graph"
      aria-label={`Voice ${metric} over the last ${VOICE_WINDOW_S} seconds`}
    />
  )
}

function render(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  samples: VoiceSample[],
  lines: { typicalHz: number | null; spikeHz: number | null; calibrated: boolean; live: boolean },
  metric: Props['metric']
): void {
  const { ticks, unit } = SCALES[metric]
  const left = 68
  const right = w - 12
  const top = 10
  const bottom = h - 22
  if (right <= left || bottom <= top) return

  const last = samples.at(-1)
  const end = (metric === 'pitch' || metric === 'wpm') && lines.live
    ? performance.now() / 1000
    : (last?.t ?? 0)
  // Let short volume recordings fill the chart, then scroll once 15 seconds have been captured.
  const windowSeconds =
    metric === 'wpm' && samples.length
      ? Math.max(VOICE_WINDOW_S, end - samples[0].t)
      : metric === 'volume' && last
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
  const dashed = (hz: number, color: string, label: string): void => {
    ctx.strokeStyle = color
    ctx.setLineDash([6, 4])
    ctx.beginPath()
    ctx.moveTo(left, y(hz))
    ctx.lineTo(right, y(hz))
    ctx.stroke()
    ctx.setLineDash([])
    ctx.fillStyle = color
    ctx.textAlign = 'right'
    ctx.textBaseline = 'bottom'
    ctx.fillText(label, right - 4, Math.max(y(hz) - 3, top + 12))
  }
  if (lines.spikeHz)
    dashed(lines.spikeHz, COLORS.spike, `Pitch spike ${lines.spikeHz.toFixed(0)} Hz`)
  if (lines.typicalHz)
    dashed(
      lines.typicalHz,
      COLORS.typical,
      `${lines.calibrated ? 'Calibrated' : 'Typical'} ${lines.typicalHz.toFixed(0)} Hz`
    )

  // Break the trace when readings are missing.
  ctx.save()
  ctx.beginPath()
  ctx.rect(left, top - 3, right - left, bottom - top + 6)
  ctx.clip()
  ctx.strokeStyle = COLORS.trace
  ctx.lineWidth = 2
  ctx.lineJoin = 'round'
  ctx.beginPath()
  let prev: VoiceSample | null = null
  for (const s of samples) {
    if (s.t < start - JOIN_S || s.value === null) {
      prev = null
      continue
    }
    if (prev && s.t - prev.t <= JOIN_S) ctx.lineTo(x(s.t), y(s.value))
    else ctx.moveTo(x(s.t), y(s.value))
    prev = s
  }
  ctx.stroke()
  ctx.restore()
  // single isolated readings would be invisible as a path; mark the latest one
  if (last && last.value !== null && last.t >= start && last.t >= end - JOIN_S) {
    ctx.fillStyle = COLORS.trace
    ctx.beginPath()
    ctx.arc(x(last.t), y(last.value), 3, 0, 2 * Math.PI)
    ctx.fill()
  }
}
