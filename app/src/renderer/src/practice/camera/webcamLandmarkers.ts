// MediaPipe pose + hand + face landmarkers running in the renderer on the GPU (WebGL).
// Model .task files are served from the repo-level models/ folder (see electron.vite.config.ts);
// the paths are relative so they work from both the dev server and app:// in production.

import { FaceLandmarker, HandLandmarker, PoseLandmarker } from '@mediapipe/tasks-vision'
import wasmLoaderPath from '@mediapipe/tasks-vision/vision_wasm_internal.js?url'
import wasmBinaryPath from '@mediapipe/tasks-vision/vision_wasm_internal.wasm?url'

import type { FaceData, FrameMessage, HandData, Point3, PoseLandmark } from './webcamProtocol'

// the package doesn't export its WasmFileset type
const WASM: Parameters<typeof PoseLandmarker.createFromOptions>[0] = {
  wasmLoaderPath,
  wasmBinaryPath
}

export interface Landmarkers {
  pose: PoseLandmarker
  hands: HandLandmarker
  face: FaceLandmarker
  faceCrop: FaceLandmarker // same model, single images: the enlarged head crop (see findFace)
  crop: HTMLCanvasElement
  close(): void
}

// MediaPipe's face detector is short-range: a face that's small in the frame (a few metres from
// the camera) often isn't found at all. When that happens but the pose model sees the head, the
// head is cut out, enlarged to CROP_SIZE and searched again.
const CROP_SIZE = 256
const HEAD_POINTS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10] // pose: nose, eyes, ears, mouth
const HEAD_BOX_EARS = 2.4 // crop side, in ear-to-ear widths (the face with some margin)

export async function createLandmarkers(): Promise<Landmarkers> {
  const base = (model: string): { modelAssetPath: string; delegate: 'GPU' } => ({
    modelAssetPath: model,
    delegate: 'GPU' as const
  })
  const faceOptions = {
    baseOptions: base('./face.task'),
    numFaces: 1,
    outputFaceBlendshapes: true
  }
  const [pose, hands, face, faceCrop] = await Promise.all([
    PoseLandmarker.createFromOptions(WASM, {
      baseOptions: base('./pose.task'),
      runningMode: 'VIDEO',
      numPoses: 1
    }),
    HandLandmarker.createFromOptions(WASM, {
      baseOptions: base('./hand.task'),
      runningMode: 'VIDEO',
      numHands: 2
    }),
    FaceLandmarker.createFromOptions(WASM, { ...faceOptions, runningMode: 'VIDEO' }),
    FaceLandmarker.createFromOptions(WASM, { ...faceOptions, runningMode: 'IMAGE' })
  ])
  const crop = document.createElement('canvas')
  crop.width = crop.height = CROP_SIZE
  return {
    pose,
    hands,
    face,
    faceCrop,
    crop,
    close() {
      pose.close()
      hands.close()
      face.close()
      faceCrop.close()
    }
  }
}

const round = (v: number): number => Math.round(v * 1e4) / 1e4

// The view is mirrored like a selfie camera, so x is flipped here and everything downstream
// (drawing, Python) works in mirrored coordinates, matching webcam_test.py.
const mirror3 = (p: { x: number; y: number; z: number }): Point3 => [
  round(1 - p.x),
  round(p.y),
  round(p.z)
]

type RawPoint = { x: number; y: number; z: number; visibility?: number }
type FaceFound = { landmarks: RawPoint[]; blendshapes: { categoryName: string; score: number }[] }

/** A square around the head (pixels), from the pose model's face points; null if it can't see the
 *  head well enough. */
function headBox(
  body: RawPoint[] | undefined,
  w: number,
  h: number
): { x: number; y: number; size: number } | null {
  if (!body) return null
  const seen = HEAD_POINTS.map((i) => body[i]).filter((p) => p && (p.visibility ?? 0) > 0.5)
  const [lEar, rEar] = [body[7], body[8]]
  if (seen.length < 5 || !lEar || !rEar) return null
  const cx = (seen.reduce((s, p) => s + p.x, 0) / seen.length) * w
  const cy = (seen.reduce((s, p) => s + p.y, 0) / seen.length) * h
  const ears = Math.hypot((lEar.x - rEar.x) * w, (lEar.y - rEar.y) * h)
  const size = Math.min(Math.max(ears * HEAD_BOX_EARS, 48), w, h)
  // keep the box inside the frame so the crop is never padded
  const x = Math.min(Math.max(cx - size / 2, 0), w - size)
  const y = Math.min(Math.max(cy - size / 2, 0), h - size)
  return { x, y, size }
}

/** The face in the full frame, or (when that finds nothing) in the enlarged head crop, mapped back
 *  to full-frame coordinates. */
function findFace(
  lm: Landmarkers,
  video: HTMLVideoElement,
  ms: number,
  body: RawPoint[] | undefined
): FaceFound | null {
  const full = lm.face.detectForVideo(video, ms)
  if (full.faceLandmarks[0])
    return {
      landmarks: full.faceLandmarks[0],
      blendshapes: full.faceBlendshapes[0]?.categories ?? []
    }
  const w = video.videoWidth
  const h = video.videoHeight
  const box = headBox(body, w, h)
  if (!box) return null
  lm.crop
    .getContext('2d')!
    .drawImage(video, box.x, box.y, box.size, box.size, 0, 0, CROP_SIZE, CROP_SIZE)
  const res = lm.faceCrop.detect(lm.crop)
  const points = res.faceLandmarks[0]
  if (!points) return null
  return {
    // crop coordinates (0-1 of the box) -> frame coordinates (0-1 of the video); z is in x units
    landmarks: points.map((p) => ({
      x: (box.x + p.x * box.size) / w,
      y: (box.y + p.y * box.size) / h,
      z: (p.z * box.size) / w
    })),
    blendshapes: res.faceBlendshapes[0]?.categories ?? []
  }
}

/** Runs all three models on the current video frame. `t` is in seconds. */
export function detect(lm: Landmarkers, video: HTMLVideoElement, t: number): FrameMessage {
  const ms = t * 1000
  const poseRes = lm.pose.detectForVideo(video, ms)
  const handRes = lm.hands.detectForVideo(video, ms)
  const body = poseRes.landmarks[0]
  const found = findFace(lm, video, ms, body)
  const pose: PoseLandmark[] | null = body
    ? body.map((p) => [...mirror3(p), round(p.visibility ?? 0)] as PoseLandmark)
    : null
  poseRes.close()

  // Handedness is reported for the unmirrored image; flip it so it matches the mirrored view.
  const hands: HandData[] = handRes.landmarks.map((points, i) => {
    const cat = handRes.handedness[i]?.[0]
    const label = cat?.categoryName === 'Left' ? 'Right' : 'Left'
    return { handedness: label, score: round(cat?.score ?? 0), landmarks: points.map(mirror3) }
  })

  let face: FaceData | null = null
  if (found) {
    const blendshapes: Record<string, number> = {}
    for (const c of found.blendshapes) blendshapes[c.categoryName] = round(c.score)
    face = { landmarks: found.landmarks.map(mirror3), blendshapes }
  }

  return {
    type: 'frame',
    t,
    width: video.videoWidth,
    height: video.videoHeight,
    pose,
    hands,
    face
  }
}
