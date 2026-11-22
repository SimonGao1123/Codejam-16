// Canvas drawing over the webcam, ported from webcam_test.py. The canvas is sized to the video's
// pixel size, so the pixel sizes below match the Python version on the same camera.

import type { Style } from '../../liveAlert/ruleDefinitions'
import type { FrameMessage, OverlayColor, StateMessage } from './webcamProtocol'

export const POSE_CONNECTIONS: [number, number][] = [
  [0, 1], [1, 2], [2, 3], [3, 7], [0, 4], [4, 5], [5, 6], [6, 8],
  [11, 12], [11, 13], [13, 15], [15, 17], [15, 19], [15, 21], [17, 19],
  [12, 14], [14, 16], [16, 18], [16, 20], [16, 22], [18, 20],
  [11, 23], [12, 24], [23, 24], [23, 25], [24, 26], [25, 27], [26, 28],
  [27, 29], [28, 30], [29, 31], [30, 32], [27, 31], [28, 32]
] // prettier-ignore
const POSE_MOUTH = new Set([9, 10]) // mouth corners: not used, so not drawn
export const HAND_CONNECTIONS: [number, number][] = [
  [0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8],
  [5, 9], [9, 10], [10, 11], [11, 12], [9, 13], [13, 14], [14, 15], [15, 16],
  [13, 17], [0, 17], [17, 18], [18, 19], [19, 20]
] // prettier-ignore

export const COLORS: Record<OverlayColor, string> = {
  green: 'rgb(80, 200, 80)',
  yellow: 'rgb(255, 200, 0)',
  red: 'rgb(230, 60, 60)',
  white: 'rgb(255, 255, 255)'
}

type XY = readonly number[] // [x, y, ...] normalized

function skeleton(
  ctx: CanvasRenderingContext2D,
  points: XY[],
  connections: [number, number][],
  color: string,
  skip: Set<number> = new Set()
): void {
  const { width: w, height: h } = ctx.canvas
  ctx.strokeStyle = color
  ctx.lineWidth = 2
  ctx.beginPath()
  for (const [a, b] of connections) {
    ctx.moveTo(points[a][0] * w, points[a][1] * h)
    ctx.lineTo(points[b][0] * w, points[b][1] * h)
  }
  ctx.stroke()
  ctx.fillStyle = COLORS.white
  points.forEach((p, i) => {
    if (skip.has(i)) return
    ctx.beginPath()
    ctx.arc(p[0] * w, p[1] * h, 3, 0, 2 * Math.PI)
    ctx.fill()
  })
}

/** Text with a black outline so it reads on any background. */
function outlined(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  color: string,
  px: number
): void {
  ctx.font = `bold ${px}px -apple-system, "Segoe UI", sans-serif`
  ctx.lineJoin = 'round'
  ctx.lineWidth = Math.max(3, px / 4)
  ctx.strokeStyle = 'black'
  ctx.strokeText(text, x, y)
  ctx.fillStyle = color
  ctx.fillText(text, x, y)
}

/** Pose (green) and hand (yellow) skeletons, drawn straight from the landmarks. */
export function drawLandmarks(ctx: CanvasRenderingContext2D, frame: FrameMessage): void {
  if (frame.pose) skeleton(ctx, frame.pose, POSE_CONNECTIONS, COLORS.green, POSE_MOUTH)
  for (const hand of frame.hands) skeleton(ctx, hand.landmarks, HAND_CONNECTIONS, COLORS.yellow)
}

/** Everything Python computed: rest circles, hand labels, status lines, alerts, countdown.
 *  Alerts are drawn in the style chosen on the Live alerts tab. */
export function drawOverlay(
  ctx: CanvasRenderingContext2D,
  state: StateMessage | null,
  local: {
    fps: number
    handsSeen: number
    connected: boolean
    wpm: number | null
    wpmUpdatedAt: number | null
    avgWpm: number | null // last 3 finished lines
    desiredWpm: number | null // the pace to aim for
  },
  alertStyle: Style
): void {
  const { width: w, height: h } = ctx.canvas
  let y = 28
  const freshWpm =
    local.wpmUpdatedAt !== null && performance.now() / 1000 - local.wpmUpdatedAt < 1
      ? local.wpm
      : null
  outlined(
    ctx,
    `FPS ${local.fps.toFixed(0)}   hands seen: ${local.handsSeen}   WPM: ${freshWpm?.toFixed(0) ?? '--'}` +
      `   avg: ${local.avgWpm?.toFixed(0) ?? '--'} / ${local.desiredWpm?.toFixed(0) ?? '--'} desired`,
    12,
    y,
    COLORS.white,
    18
  )

  if (!local.connected || !state) {
    outlined(ctx, 'analysis service not connected yet (starting...)', 12, y + 26, COLORS.yellow, 18)
    return
  }

  for (const c of state.rest_circles) {
    ctx.strokeStyle = c.active ? COLORS.green : COLORS.white
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.arc(c.x * w, c.y * h, c.r * w, 0, 2 * Math.PI)
    ctx.stroke()
  }

  for (const l of state.hand_labels)
    outlined(ctx, l.text, l.x * w + 10, l.y * h + 20, COLORS.yellow, 18)

  for (const line of state.lines) {
    y += 26
    outlined(ctx, line.text, 12, y, COLORS[line.color], 18)
  }

  const cal = state.calibration
  if (cal.message)
    outlined(ctx, cal.message, 12, y + 26, cal.calibrated ? COLORS.green : COLORS.yellow, 18)

  const top = Math.max(150, y + 26 + 70) // below the status lines and calibration message
  if (alertStyle === 'banner') state.alerts.forEach((text, i) => banner(ctx, top, i, text))
  if (alertStyle === 'compact') state.alerts.forEach((text, i) => pill(ctx, i, text))
  // 'silent': no alerts on screen
  if (cal.phase === 'arming') {
    // the service says what's missing: "Waiting to calibrate: show both hands, hold still"
    const todo = cal.message.split(': ').slice(1).join(': ') || 'get in frame and hold still'
    countdown(ctx, null, todo.charAt(0).toUpperCase() + todo.slice(1))
  }
  if (cal.phase === 'waiting') countdown(ctx, Math.ceil(cal.remaining), 'Get into position')
  if (cal.phase === 'recording')
    countdown(ctx, Math.max(1, Math.ceil(cal.remaining)), 'Stand still in your resting position')
}

// Alert look, matching the Live alerts preview (.la-cue in liveAlerts.css)
const CUE = { fill: '#f6d58e', border: '#ecc981', text: '#33250d' }
const CUE_FONT = '-apple-system, "Segoe UI", sans-serif'

function cueBox(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number
): void {
  ctx.beginPath()
  ctx.roundRect(x, y, w, h, r)
  ctx.fillStyle = CUE.fill
  ctx.fill()
  ctx.lineWidth = 2
  ctx.strokeStyle = CUE.border
  ctx.stroke()
}

/** Banner: a wide box across the video with the cue centered; i stacks multiple alerts. */
function banner(ctx: CanvasRenderingContext2D, top: number, i: number, text: string): void {
  const px = 34
  const margin = 40
  const boxH = px + 28
  const y = top + i * (boxH + 12)
  cueBox(ctx, margin, y, ctx.canvas.width - 2 * margin, boxH, 12)
  ctx.font = `600 ${px}px ${CUE_FONT}`
  ctx.fillStyle = CUE.text
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(text, ctx.canvas.width / 2, y + boxH / 2)
  ctx.textAlign = 'start'
  ctx.textBaseline = 'alphabetic'
}

/** Compact: a small pill at the top-right edge of the video; i stacks multiple alerts. */
function pill(ctx: CanvasRenderingContext2D, i: number, text: string): void {
  const px = 22
  const padX = 18
  const boxH = px + 18
  ctx.font = `600 ${px}px ${CUE_FONT}`
  const tw = ctx.measureText(text).width
  const x = ctx.canvas.width - 24 - (tw + 2 * padX)
  const y = 24 + i * (boxH + 10)
  cueBox(ctx, x, y, tw + 2 * padX, boxH, boxH / 2)
  ctx.fillStyle = CUE.text
  ctx.textBaseline = 'middle'
  ctx.fillText(text, x + padX, y + boxH / 2)
  ctx.textBaseline = 'alphabetic'
}

/** Large countdown number (if any) in the middle of the frame with an instruction under it, readable
 *  from a few steps back. */
function countdown(ctx: CanvasRenderingContext2D, n: number | null, caption: string): void {
  const px = 160
  const baseline = (ctx.canvas.height + px * 0.7) / 2
  if (n !== null) {
    const text = String(n)
    ctx.font = `bold ${px}px -apple-system, "Segoe UI", sans-serif`
    const tw = ctx.measureText(text).width
    outlined(ctx, text, (ctx.canvas.width - tw) / 2, baseline, COLORS.yellow, px)
  }
  // the caption wraps onto several centred lines so a long instruction stays inside the video
  const cpx = 40
  ctx.font = `bold ${cpx}px -apple-system, "Segoe UI", sans-serif`
  const lines = wrap(ctx, caption, ctx.canvas.width - 80)
  lines.forEach((text, i) => {
    const cw = ctx.measureText(text).width
    const y = baseline + cpx + 30 + i * (cpx + 12)
    outlined(ctx, text, (ctx.canvas.width - cw) / 2, y, COLORS.yellow, cpx)
  })
}

/** Splits text into lines no wider than maxWidth in the current font. */
function wrap(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const lines: string[] = []
  let current = ''
  for (const word of text.split(' ')) {
    const next = current ? `${current} ${word}` : word
    if (current && ctx.measureText(next).width > maxWidth) {
      lines.push(current)
      current = word
    } else current = next
  }
  if (current) lines.push(current)
  return lines
}
