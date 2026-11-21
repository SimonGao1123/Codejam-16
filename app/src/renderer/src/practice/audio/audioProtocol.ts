// Messages between the Electron app and the Python analysis service's audio side
// (ws://127.0.0.1:<port>/audio). Full description: docs/audio-protocol.md. Keep the two in sync.
//
// Electron streams the practice session's microphone as raw audio; Python measures pitch and
// volume, runs speech-to-text with filler-word detection, and sends back voice readings and
// transcript updates. Voice and filler alerts also appear as banners on the webcam overlay.

/** The service is started by the main process on a free port (src/main/analysisService.ts). */
export const audioAnalysisUrl = (): string => `ws://127.0.0.1:${window.api.servicePort}/audio`
export const AUDIO_SAMPLE_RATE = 16000 // binary messages: float32 little-endian, mono

// ---- Electron -> Python (besides the binary audio)

export type AudioControlMessage =
  | { type: 'session_start'; started_at: number } // renderer monotonic seconds at recording start
  | { type: 'session_end' } // practice ended: Python sends the final transcript and score

// ---- Python -> Electron

export const FILLER_WORDS = ['um', 'uh', 'like', 'you know', 'so', 'filler?'] as const
export type FillerWord = (typeof FILLER_WORDS)[number] // 'filler?' = sounded like um/uh, unconfirmed

export interface VoiceMessage {
  type: 'voice' // every 100 ms
  captured_at?: number | null // renderer monotonic seconds of the latest measured sample
  pitch_hz: number | null // null when not voiced (silence, breath)
  pitches: (number | null)[] // every 50 ms reading since the last message, oldest first
  volume_dbfs: number
  typical_hz: number | null // calibrated pitch, or the running typical pitch before calibrating
  spike_hz: number | null // a "Steady your pitch" alert fires above this
  calibrated: boolean
  calibrating: boolean
  message: string // calibration status, e.g. "Voice calibrated: 180 Hz, -30 dBFS"
}

export interface TranscriptMessage {
  type: 'transcript' // whenever the transcript changes
  new_sentences: string[] // finished sentences to append, e.g. "So I think [um] it works."
  partial: string // words of the sentence still being spoken (replaces the previous partial)
  new_fillers: FillerWord[] // fillers detected since the last message
  fillers: Record<FillerWord, number> // running totals for the session
  total: number
}

type ScoreCategory = 'filler' | 'voice_tone' | 'body_language'

export interface PlaybackCheckpoint {
  at: number
  category: ScoreCategory
  label: string
}

export interface SessionScoreMessage {
  type: 'session_score'
  score: number | null
  max_score: number
  checkpoints: PlaybackCheckpoint[]
  measured_points: number
  categories: Record<
    ScoreCategory,
    {
      points: number | null
      max_points: number
      deduction: number
      checks: Record<
        string,
        {
          count: number
          max_points: number
          deduction: number
          feedback: string | null
          suspected_count?: number
        }
      >
    }
  >
  feedback: { category: ScoreCategory; rule: string; message: string; deduction: number }[]
}

export type AudioServerMessage = VoiceMessage | TranscriptMessage | SessionScoreMessage
