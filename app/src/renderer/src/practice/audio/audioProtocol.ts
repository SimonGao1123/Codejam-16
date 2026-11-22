// Messages between the Electron app and the Python analysis service's audio side
// (ws://127.0.0.1:<port>/audio). Full description: docs/audio-protocol.md. Keep the two in sync.
//
// Electron streams the practice session's microphone as raw audio; Python measures pitch and
// volume, runs speech-to-text with filler-word detection, and sends back voice readings and
// transcript updates. Voice and filler alerts also appear as banners on the webcam overlay.

/** The service is started by the main process on a free port (src/main/analysisService.ts). */
export const audioAnalysisUrl = (): string => `ws://127.0.0.1:${window.api.servicePort}/audio`
export const AUDIO_SAMPLE_RATE = 16000 // binary messages: float32 little-endian, mono

export interface VoiceBaseline {
  pitch_hz: number
  volume_dbfs: number
}

// ---- Electron -> Python (besides the binary audio)

export type AudioControlMessage =
  | {
      type: 'session_start'
      started_at: number
      voice_baseline: VoiceBaseline | null
      recording_baseline?: boolean
    }
  | { type: 'session_end' } // practice ended: Python sends the final transcript and score
  | ScriptControlMessage
  | TargetTimeMessage
  // the room's noise level, measured while the user stood still during the get-ready countdown;
  // Python counts audio up to ~10 dB above it as "not speaking". Sent just before session_start.
  | { type: 'noise_floor'; dbfs: number | null }

/** The user's script (pasted or read from a PDF); null / empty = no script, comparison off.
 *  Python compares the transcript with it and keeps it across sessions until it changes. */
export interface ScriptControlMessage {
  type: 'script'
  text: string | null
}

/** The time the user has to present; null = not set. With a script, Python sets the desired pace
 *  to script words / this time (otherwise the Live alerts Target pace). Kept across sessions. */
export interface TargetTimeMessage {
  type: 'target_time'
  seconds: number | null
}

// ---- Python -> Electron

export const FILLER_WORDS = ['um', 'uh', 'like', 'you know', 'so', 'filler?'] as const
export type FillerWord = (typeof FILLER_WORDS)[number] // 'filler?' = sounded like um/uh, unconfirmed

export interface VoiceMessage {
  type: 'voice' // every 100 ms
  alerts?: string[] // current audio banners, also available without the camera
  captured_at?: number | null // renderer monotonic seconds of the latest measured sample
  pitch_hz: number | null // null when not voiced (silence, breath)
  pitches: (number | null)[] // every 50 ms reading since the last message, oldest first
  wpm: number | null // provisional WPM from Moonshine's partial words; refreshes every 100 ms
  pitch_times?: (number | null)[] // capture time for each entry in pitches
  volume_dbfs: number
  typical_hz: number | null // fixed pitch from voice calibration
  typical_volume_dbfs: number | null // fixed volume from voice calibration
  baseline_source?: 'calibration' | 'recording' | null
  quiet_limit_dbfs?: number | null
  spike_hz: number | null // upper pitch limit; the lower limit is the same interval below typical_hz
  speaking: boolean // louder than the room's silence level
  silence_dbfs: number | null // not speaking below this (null = room not measured)
}

export interface TranscriptMessage {
  type: 'transcript' // whenever the transcript changes
  new_sentences: string[] // finished sentences to append, e.g. "So I think [um] it works."
  partial: string // words of the sentence still being spoken (replaces the previous partial)
  new_fillers: FillerWord[] // fillers detected since the last message
  fillers: Record<FillerWord, number> // running totals for the session
  total: number
  wpm: number | null // recent rate, including the provisional words of the line being spoken
  avg_wpm?: number | null // last 3 finished lines (each ends at a pause), pauses included
  desired_wpm?: number // the pace to aim for (see PaceTargetMessage)
  desired_source?: PaceSource
}

/** script = script words / the time the user has; setting = the Live alerts Target pace slider */
export type PaceSource = 'script' | 'setting'

export interface PaceTargetMessage {
  type: 'pace_target' // reply to `target_time` and `script`
  desired_wpm: number
  source: PaceSource
  script_words: number
  target_seconds: number | null
}

/** Acknowledges an upload audio chunk after the service has handed it to the analyzer. */
export interface AudioChunkProcessedMessage {
  type: 'audio_processed'
  id: number
}

export interface ScriptLoadedMessage {
  type: 'script_loaded' // reply to `script`, and again once the comparison model has loaded
  chunks: string[] // the script split into parts ([] = no script)
  ready: boolean // false while the comparison model is still loading
}

export interface ScriptPositionMessage {
  type: 'script_position' // at most every 1.5 s while new words arrive
  position: number // current part (0-based), -1 = not started
  best: number // best-matching part for the latest words
  status: string // on track / jumped ahead / went back / stuck / ad-libbing ... / off topic / checking
  skipped: number[] // parts skipped so far
  best_score: number // 0-1, how well the latest words match the best part
  events: string[] // what just happened, e.g. "Jumped ahead to chunk 7: skipped 4, 5, 6"
}

type ScoreCategory = 'filler' | 'voice_tone' | 'speaking_pace' | 'body_language' | 'script_accuracy'

export interface PlaybackCheckpoint {
  at: number
  category: ScoreCategory | 'volume'
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
          average_wpm?: number
          target_wpm?: number
        }
      >
    }
  >
  feedback: { category: ScoreCategory; rule: string; message: string; deduction: number }[]
}

export type AudioServerMessage =
  | VoiceMessage
  | TranscriptMessage
  | ScriptLoadedMessage
  | ScriptPositionMessage
  | PaceTargetMessage
  | AudioChunkProcessedMessage
  | SessionScoreMessage
