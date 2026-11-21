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
  | { type: 'session_start' } // new practice session: Python clears the transcript and counts
  | { type: 'session_end' } // practice ended: Python finishes the open sentence

// ---- Python -> Electron

export const FILLER_WORDS = ['um', 'uh', 'like', 'you know', 'so', 'filler?'] as const
export type FillerWord = (typeof FILLER_WORDS)[number] // 'filler?' = sounded like um/uh, unconfirmed

export interface VoiceMessage {
  type: 'voice' // every 100 ms
  pitch_hz: number | null // null when not voiced (silence, breath)
  volume_dbfs: number
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

export type AudioServerMessage = VoiceMessage | TranscriptMessage
