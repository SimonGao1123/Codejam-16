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
  close(): void
}

export async function createLandmarkers(): Promise<Landmarkers> {
  const base = (model: string): { modelAssetPath: string; delegate: 'GPU' } => ({
    modelAssetPath: model,
    delegate: 'GPU' as const
  })
  const [pose, hands, face] = await Promise.all([
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
    FaceLandmarker.createFromOptions(WASM, {
      baseOptions: base('./face.task'),
      runningMode: 'VIDEO',
      numFaces: 1,
      outputFaceBlendshapes: true
    })
  ])
  return {
    pose,
    hands,
    face,
    close() {
      pose.close()
      hands.close()
      face.close()
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

/** Runs all three models on the current video frame. `t` is in seconds. */
export function detect(lm: Landmarkers, video: HTMLVideoElement, t: number): FrameMessage {
  const ms = t * 1000
  const poseRes = lm.pose.detectForVideo(video, ms)
  const handRes = lm.hands.detectForVideo(video, ms)
  const faceRes = lm.face.detectForVideo(video, ms)

  const body = poseRes.landmarks[0]
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
  if (faceRes.faceLandmarks[0]) {
    const blendshapes: Record<string, number> = {}
    for (const c of faceRes.faceBlendshapes[0]?.categories ?? [])
      blendshapes[c.categoryName] = round(c.score)
    face = { landmarks: faceRes.faceLandmarks[0].map(mirror3), blendshapes }
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
