// Messages between the Electron app and the Python analysis service (ws://127.0.0.1:<port>/webcam).
// Full description: docs/webcam-protocol.md. Keep the two in sync.
//
// Electron runs MediaPipe and draws the skeleton itself (no lag). Every processed frame it
// sends the few landmarks the analysis uses; Python does all the calculations and replies with what
// changed in the overlay state that Electron draws on top of the video.

import type { RuleSettings } from '../../liveAlert/ruleDefinitions'
import type { StatPart } from '../../liveAlert/webcamParts'

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

/** Everything MediaPipe found in one camera frame. Drawn by webcamDraw.ts; Python gets compactFrame(). */
export interface FrameData {
  t: number // seconds, monotonic (performance.now() / 1000); use this, not wall-clock time
  width: number // video size in pixels, to turn normalized coords into pixels
  height: number
  pose: PoseLandmark[] | null // 33 pose landmarks, or null if no body
  hands: HandData[] // 0-2 hands
  face: FaceData | null
}

// ---- Electron -> Python

// the landmarks the analysis uses, in the order Python expects them (webcam_analysis.py)
const POSE_LANDMARKS = [11, 12, 15, 16] // shoulders, wrists
const HAND_LANDMARKS = [0, 4, 8, 12, 20, 9] // wrist, four fingertips, middle knuckle
const FACE_LANDMARKS = [10, 152, 234, 454] // forehead, chin, both sides of the face

export interface FrameMessage {
  type: 'frame'
  t: number
  width: number
  height: number
  pose: [number, number, number][] | null // POSE_LANDMARKS: [x, y, visibility]
  hands: { side: 'L' | 'R'; points: [number, number][] }[] // HAND_LANDMARKS: [x, y]
  face: {
    points: number[][] // FACE_LANDMARKS: [x, y, z] for the forehead and chin, [x, y] for the sides
    eyes_down: [number, number] // blendshapes eyeLookDownLeft, eyeLookDownRight
  } | null
}

/** What Python needs of a frame: about 3% of the size of everything MediaPipe found. */
export function compactFrame(frame: FrameData): FrameMessage {
  const { pose, hands, face } = frame
  return {
    type: 'frame',
    t: Math.round(frame.t * 1e4) / 1e4,
    width: frame.width,
    height: frame.height,
    pose: pose && POSE_LANDMARKS.map((i) => [pose[i][0], pose[i][1], pose[i][3]]),
    hands: hands.map((hand) => ({
      side: hand.handedness === 'Left' ? 'L' : 'R',
      points: HAND_LANDMARKS.map((i) => [hand.landmarks[i][0], hand.landmarks[i][1]])
    })),
    face: face && {
      points: FACE_LANDMARKS.map((i, n) => face.landmarks[i].slice(0, n < 2 ? 3 : 2)),
      eyes_down: [face.blendshapes.eyeLookDownLeft ?? 0, face.blendshapes.eyeLookDownRight ?? 0]
    }
  }
}

export interface CalibrateMessage {
  type: 'calibrate' // sent automatically at the start of each session; Python records the resting position
  t: number
  delay?: number // seconds before recording (default 2); 0 for the automatic one at session start
  duration?: number // seconds of recording (default 2)
  when_ready?: boolean // wait until the face, shoulders and both hands are in frame and still
  request_id?: string // identifies the camera stream requesting calibration
}

export interface SettingsMessage {
  type: 'settings' // the Settings tab's rules; sent on connect and whenever they change
  rules: Record<string, RuleSettings> // rule id (ruleDefinitions.ts) -> settings
  display: string[] // the stats (and 'rest') being shown; Python leaves the others out of its replies
}

export type ClientMessage = FrameMessage | CalibrateMessage | SettingsMessage

// ---- Python -> Electron

export type OverlayColor = 'white' | 'red' | 'yellow' | 'green'

export interface StatusLine {
  stat: StatPart | 'body' // what it shows, so it can be turned off; body = no body detected
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
  // arming = waiting for face, shoulders and both hands in frame + holding still; waiting = timed
  // get-into-position pause; recording = measuring the resting position
  phase: 'idle' | 'arming' | 'waiting' | 'recording'
  remaining: number // seconds left in the current phase (0 when idle)
  calibrated: boolean
  request_id?: string | null
  message: string
}

/** The client keeps the latest of each field; a reply only has `type`, `t` and the fields that changed. */
export interface StateMessage {
  type: 'state'
  t: number // the frame time this state answers
  lines: StatusLine[] // status lines, drawn top-left in order
  alerts: string[] // active alert banners, e.g. "Look up"
  rest_circles: RestCircle[]
  hand_labels: HandLabel[]
  calibration: CalibrationState
}

export type ServerMessage = Partial<StateMessage> & Pick<StateMessage, 'type' | 't'>
