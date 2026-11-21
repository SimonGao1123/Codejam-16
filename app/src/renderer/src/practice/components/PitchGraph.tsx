// Rolling pitch graph for the practice session, like experiments/gap_heuristic/voice_pitch_gui.py:
// the last 15 s of voice pitch, the speaker's typical (or calibrated) pitch as a dashed line, and
// the pitch where a "Steady your pitch" alert would fire. Redrawn every animation frame from a
// buffer the transcript panel fills, so the 10 readings a second don't re-render React.

import { useEffect, useRef } from 'react'

export interface PitchSample {
  t: number // seconds (performance.now() / 1000)
  hz: number | null // null = not voiced (silence, breath)
}

type Props = {
  samples: React.RefObject<PitchSample[]>
  typicalHz: number | null
  spikeHz: number | null
  calibrated: boolean
  live: boolean // false: freeze on the last reading (session ended)
}

export const PITCH_WINDOW_S = 15
const TICKS = [50, 100, 200, 300, 400, 500] // Hz, linear scale like the Python GUI
const JOIN_S = 0.25 // readings further apart than this aren't joined (a pause in speech)
const COLORS = {
  grid: '#38334e',
  label: '#8f89a8',
  trace: '#c4aeff',
  typical: '#8fd6a1',
  spike: '#f08a8a'
}
const FONT = '12px -apple-system, "Segoe UI", sans-serif'

export default function PitchGraph({
  samples,
  typicalHz,
  spikeHz,
  calibrated,
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
      render(ctx, cssW, cssH, samples.current ?? [], linesRef.current)
    }
    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [samples])

  return (
    <canvas
      ref={canvasRef}
      className="ph-pitch-graph"
      aria-label="Voice pitch over the last 15 seconds"
    />
  )
}

function render(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  samples: PitchSample[],
  lines: { typicalHz: number | null; spikeHz: number | null; calibrated: boolean; live: boolean }
): void {
  const left = 58
  const right = w - 12
  const top = 10
  const bottom = h - 22
  if (right <= left || bottom <= top) return

  const end = lines.live ? performance.now() / 1000 : (samples.at(-1)?.t ?? 0)
  const start = end - PITCH_WINDOW_S
  const x = (t: number): number => left + ((t - start) / PITCH_WINDOW_S) * (right - left)
  const y = (hz: number): number => {
    const v = Math.min(TICKS.at(-1)!, Math.max(TICKS[0], hz))
    return bottom - ((v - TICKS[0]) / (TICKS.at(-1)! - TICKS[0])) * (bottom - top)
  }

  ctx.font = FONT
  ctx.lineWidth = 1
  // horizontal grid + Hz labels
  ctx.textAlign = 'right'
  ctx.textBaseline = 'middle'
  for (const hz of TICKS) {
    ctx.strokeStyle = COLORS.grid
    ctx.beginPath()
    ctx.moveTo(left, y(hz))
    ctx.lineTo(right, y(hz))
    ctx.stroke()
    ctx.fillStyle = COLORS.label
    ctx.fillText(`${hz} Hz`, left - 8, y(hz))
  }
  // time axis: seconds ago
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  for (let ago = PITCH_WINDOW_S; ago >= 0; ago -= 5) {
    ctx.fillText(ago ? `-${ago}s` : 'now', x(end - ago), bottom + 6)
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

  // pitch trace, broken at pauses
  ctx.strokeStyle = COLORS.trace
  ctx.lineWidth = 2
  ctx.lineJoin = 'round'
  ctx.beginPath()
  let prev: PitchSample | null = null
  for (const s of samples) {
    if (s.t < start - JOIN_S || s.hz === null) {
      prev = null
      continue
    }
    if (prev && s.t - prev.t <= JOIN_S) ctx.lineTo(x(s.t), y(s.hz))
    else ctx.moveTo(x(s.t), y(s.hz))
    prev = s
  }
  ctx.stroke()
  // single isolated readings would be invisible as a path; mark the latest one
  const last = samples.at(-1)
  if (last?.hz && last.t >= start) {
    ctx.fillStyle = COLORS.trace
    ctx.beginPath()
    ctx.arc(x(last.t), y(last.hz), 3, 0, 2 * Math.PI)
    ctx.fill()
  }
}
