// Messages between the Electron app and the Python analysis service (ws://127.0.0.1:<port>/webcam).
// Full description: docs/webcam-protocol.md. Keep the two in sync.
//
// Electron runs MediaPipe and draws the skeleton itself (no lag). Every processed frame it
// sends the raw landmarks; Python does all the calculations and replies with an overlay
// state that Electron draws on top of the video, the same way webcam_test.py does.

/** The service is started by the main process on a free port (src/main/analysisService.ts). */
export const webcamAnalysisUrl = (): string => `ws://127.0.0.1:${window.api.servicePort}/webcam`

/** [x, y, z, visibility]: x/y normalized 0-1 in the mirrored view, z in roughly x units. */
export type PoseLandmark = [number, number, number, number]
/** [x, y, z]: x/y normalized 0-1 in the mirrored view, z in roughly x units. */
export type Point3 = [number, number, number]

export interface HandData {
  handedness: 'Left' | 'Right' // as seen in the mirrored view (matches webcam_test.py)
  score: number
  landmarks: Point3[] // 21 hand landmarks
}

export interface FaceData {
  landmarks: Point3[] // 478 face landmarks
  blendshapes: Record<string, number> // 52 scores, e.g. eyeLookDownLeft
}

// ---- Electron -> Python

export interface FrameMessage {
  type: 'frame'
  t: number // seconds, monotonic (performance.now() / 1000); use this, not wall-clock time
  width: number // video size in pixels, to turn normalized coords into pixels
  height: number
  pose: PoseLandmark[] | null // 33 pose landmarks, or null if no body
  hands: HandData[] // 0-2 hands
  face: FaceData | null
}

export interface CalibrateMessage {
  type: 'calibrate' // user clicked Calibrate; Python runs the countdown + recording
  t: number
}

export type ClientMessage = FrameMessage | CalibrateMessage

// ---- Python -> Electron

export type OverlayColor = 'white' | 'red' | 'yellow' | 'green'

export interface StatusLine {
  text: string
  color: OverlayColor
}

export interface RestCircle {
  x: number // center, normalized 0-1
  y: number
  r: number // radius as a fraction of the video width
  active: boolean // hand currently resting inside it (drawn green)
}

export interface HandLabel {
  x: number // normalized 0-1, where to draw the text (near the wrist)
  y: number
  text: string // e.g. "L +35 deg"
}

export interface CalibrationState {
  phase: 'idle' | 'waiting' | 'recording' // waiting = get-into-position countdown
  remaining: number // seconds left in the current phase (0 when idle)
  calibrated: boolean
  message: string
}

export interface StateMessage {
  type: 'state'
  t: number // the frame time this state answers
  lines: StatusLine[] // status lines, drawn top-left in order
  alerts: string[] // active alert banners, e.g. "Look up"
  rest_circles: RestCircle[]
  hand_labels: HandLabel[]
  calibration: CalibrationState
}

export type ServerMessage = StateMessage
