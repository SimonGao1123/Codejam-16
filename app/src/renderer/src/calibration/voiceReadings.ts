// Pitch and volume of one 50 ms block of 16 kHz audio, for the live graphs on the voice
// calibration page. A direct port of pitch_hz / volume_dbfs in service/presentation_service/voice.py
// (same frame, pitch range and confidence), so the graph shows what the service measures.

import { AUDIO_SAMPLE_RATE } from '../practice/audio/audioProtocol'

export const BLOCK_SIZE = AUDIO_SAMPLE_RATE * 0.05 // 800 samples
export const BLOCK_SECONDS = 0.05
export const MIN_PITCH_DBFS = -70 // quieter blocks aren't used for pitch (the service skips them too)
const FRAME_SIZE = AUDIO_SAMPLE_RATE * 0.04 // 640: centred in the block
const MIN_LAG = Math.floor(AUDIO_SAMPLE_RATE / 450)
const MAX_LAG = Math.floor(AUDIO_SAMPLE_RATE / 70)
const MIN_CONFIDENCE = 0.62
const PEAK_TOLERANCE = 0.02

export function volumeDbfs(block: Float32Array): number {
  let sum = 0
  for (const v of block) sum += v * v
  return 20 * Math.log10(Math.max(Math.sqrt(sum / block.length), 1e-8))
}

/** Pitch from the first strong normalized-autocorrelation peak; null when not voiced. */
export function pitchHz(block: Float32Array): number | null {
  const start = Math.floor((block.length - FRAME_SIZE) / 2)
  const frame = new Float64Array(FRAME_SIZE)
  let mean = 0
  for (let i = 0; i < FRAME_SIZE; i++) mean += block[start + i]
  mean /= FRAME_SIZE
  let anyNonZero = false
  for (let i = 0; i < FRAME_SIZE; i++) {
    frame[i] = block[start + i] - mean
    if (frame[i] !== 0) anyNonZero = true
  }
  if (!anyNonZero) return null

  // energy[k] = sum of frame[i]^2 for i < k
  const energy = new Float64Array(FRAME_SIZE + 1)
  for (let i = 0; i < FRAME_SIZE; i++) energy[i + 1] = energy[i] + frame[i] * frame[i]
  const lags: number[] = []
  const scores: number[] = []
  for (let lag = MIN_LAG - 1; lag <= MAX_LAG + 1; lag++) {
    let correlation = 0
    for (let i = 0; i + lag < FRAME_SIZE; i++) correlation += frame[i] * frame[i + lag]
    const norm = Math.sqrt(energy[FRAME_SIZE - lag] * (energy[FRAME_SIZE] - energy[lag]))
    lags.push(lag)
    scores.push(norm > 0 ? correlation / norm : 0)
  }
  const peaks: number[] = []
  for (let j = 1; j < scores.length - 1; j++)
    if (scores[j] > scores[j - 1] && scores[j] >= scores[j + 1]) peaks.push(j)
  if (!peaks.length) return null
  const best = Math.max(...peaks.map((j) => scores[j]))
  if (best < MIN_CONFIDENCE) return null
  // prefer the shortest period that explains the waveform almost as well as the best peak
  const cutoff = Math.max(MIN_CONFIDENCE, best - PEAK_TOLERANCE)
  const chosen = peaks.find((j) => scores[j] >= cutoff)!
  return AUDIO_SAMPLE_RATE / lags[chosen]
}
