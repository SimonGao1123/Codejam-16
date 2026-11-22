import { captureMic, type MicCapture } from '../practice/audio/audioCapture'
import { AUDIO_SAMPLE_RATE, type VoiceBaseline } from '../practice/audio/audioProtocol'

export const CALIBRATION_SECONDS = 6
const storageKey = 'colloquia:voice-calibration'

function isVoiceBaseline(value: unknown): value is VoiceBaseline {
  if (!value || typeof value !== 'object') return false
  const { pitch_hz: pitch, volume_dbfs: volume } = value as Partial<VoiceBaseline>
  return (
    typeof pitch === 'number' && pitch >= 60 && pitch <= 500 &&
    typeof volume === 'number' && volume >= -70 && volume <= 0
  )
}

export function loadVoiceCalibration(): VoiceBaseline | null {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(storageKey) ?? 'null')
    return isVoiceBaseline(saved) ? saved : null
  } catch {
    return null
  }
}

export function saveVoiceCalibration(baseline: VoiceBaseline): void {
  localStorage.setItem(storageKey, JSON.stringify(baseline))
}

async function recordSample(signal: AbortSignal, onListening: () => void): Promise<Float32Array> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { autoGainControl: false } })
  let capture: MicCapture | undefined
  let timeout: number | undefined
  try {
    signal.throwIfAborted()
    const sample = new Float32Array(CALIBRATION_SECONDS * AUDIO_SAMPLE_RATE)
    let written = 0
    return await new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(signal.reason), { once: true })
      timeout = window.setTimeout(
        () => reject(new Error('The microphone did not provide enough audio. Try again.')),
        12_000
      )
      void captureMic(stream, (chunk) => {
        if (written === 0) onListening()
        const count = Math.min(chunk.length, sample.length - written)
        sample.set(chunk.subarray(0, count), written)
        written += count
        if (written === sample.length) resolve(sample)
      }).then((opened) => {
        if (signal.aborted || !stream.active) opened.stop()
        else capture = opened
      }, reject)
    })
  } finally {
    window.clearTimeout(timeout)
    capture?.stop()
    stream.getTracks().forEach((track) => track.stop())
  }
}

export async function measureVoiceCalibration(
  signal: AbortSignal,
  onListening: () => void
): Promise<VoiceBaseline> {
  const sample = await recordSample(signal, onListening)
  signal.throwIfAborted()
  const socket = new WebSocket(`ws://127.0.0.1:${window.api.servicePort}/voice-calibration`)
  let timeout: number | undefined
  try {
    return await new Promise((resolve, reject) => {
      const fail = (): void => reject(new Error('Couldn’t reach voice analysis. Try again.'))
      signal.addEventListener('abort', () => reject(signal.reason), { once: true })
      timeout = window.setTimeout(fail, 8_000)
      socket.onopen = () => socket.send(sample.buffer)
      socket.onerror = fail
      socket.onclose = fail
      socket.onmessage = ({ data }) => {
        try {
          const { baseline, error } = JSON.parse(data)
          if (isVoiceBaseline(baseline)) resolve(baseline)
          else reject(new Error(typeof error === 'string' ? error : 'Couldn’t measure your voice.'))
        } catch {
          fail()
        }
      }
    })
  } finally {
    window.clearTimeout(timeout)
    socket.close()
  }
}
