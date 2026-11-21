// Running transcript + filler word counter, below the camera preview. While the practice
// session's microphone is on, its audio streams to the Python service, which sends back the
// transcript, filler counts and voice readings.

import { useEffect, useRef, useState } from 'react'

import { AudioAnalysisClient } from './audioAnalysisClient'
import { captureMic } from './audioCapture'
import {
  FILLER_WORDS,
  type FillerWord,
  type TranscriptMessage,
  type VoiceMessage
} from './audioProtocol'

type Props = {
  micStream: MediaStream | null
  running: boolean
}

const NO_FILLERS = Object.fromEntries(FILLER_WORDS.map((w) => [w, 0])) as Record<FillerWord, number>
const FLASH_MS = 1500 // how long a filler chip stays highlighted after it's counted

export default function TranscriptPanel({ micStream, running }: Props): React.JSX.Element {
  const clientRef = useRef<AudioAnalysisClient | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [connected, setConnected] = useState(false)
  const [sentences, setSentences] = useState<string[]>([])
  const [partial, setPartial] = useState('')
  const [fillers, setFillers] = useState(NO_FILLERS)
  const [total, setTotal] = useState(0)
  const [flash, setFlash] = useState<Partial<Record<FillerWord, number>>>({})
  const [voice, setVoice] = useState<VoiceMessage | null>(null)

  // connection to the Python service: for as long as the practice page is open
  useEffect(() => {
    const client = new AudioAnalysisClient()
    clientRef.current = client
    client.onConnectionChange = setConnected
    client.onMessage = (msg) => {
      if (msg.type === 'voice') setVoice(msg)
      else applyTranscript(msg)
    }
    function applyTranscript(msg: TranscriptMessage): void {
      if (msg.new_sentences.length) setSentences((s) => [...s, ...msg.new_sentences])
      setPartial(msg.partial)
      setFillers(msg.fillers)
      setTotal(msg.total)
      if (msg.new_fillers.length) {
        const now = Date.now()
        setFlash((f) => ({ ...f, ...Object.fromEntries(msg.new_fillers.map((w) => [w, now])) }))
      }
    }
    return () => {
      client.close()
      clientRef.current = null
    }
  }, [])

  // stream the mic while the session has one; a new session starts a fresh transcript
  useEffect(() => {
    const client = clientRef.current
    if (!micStream || !client) return
    let stopped = false
    let capture: { stop(): void } | null = null
    setSentences([])
    setPartial('')
    setFillers(NO_FILLERS)
    setTotal(0)
    setVoice(null)
    client.send({ type: 'session_start' })
    captureMic(micStream, (samples) => client.sendAudio(samples)).then(
      (c) => (stopped ? c.stop() : (capture = c)),
      (err) => console.error('microphone capture failed', err)
    )
    return () => {
      stopped = true
      capture?.stop()
      client.send({ type: 'session_end' })
    }
  }, [micStream])

  // keep the newest words in view
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [sentences, partial])

  // un-highlight filler chips after FLASH_MS
  useEffect(() => {
    if (!Object.keys(flash).length) return
    const timer = window.setTimeout(() => setFlash({}), FLASH_MS)
    return () => window.clearTimeout(timer)
  }, [flash])

  const listening = Boolean(micStream) && connected
  const empty = !sentences.length && !partial

  return (
    <section className="ph-transcript" aria-labelledby="ph-transcript-heading">
      <div className="ph-card-head">
        <h2 id="ph-transcript-heading">Transcript</h2>
        <span>
          {!connected
            ? 'Speech analysis not connected'
            : listening
              ? voice?.calibrating
                ? voice.message
                : voice?.pitch_hz
                  ? `Listening · ${voice.pitch_hz.toFixed(0)} Hz · ${voice.volume_dbfs.toFixed(0)} dBFS`
                  : 'Listening'
              : running
                ? 'Microphone off'
                : 'Starts with your practice session'}
        </span>
      </div>
      <div className="ph-fillers" aria-label={`Filler words: ${total}`}>
        <div className="ph-filler-total">
          <strong>{total}</strong>
          <span>filler words</span>
        </div>
        {FILLER_WORDS.map((word) => (
          <span key={word} className="ph-filler" data-flash={Boolean(flash[word])}>
            {word === 'filler?' ? 'um?' : word} <b>{fillers[word] ?? 0}</b>
          </span>
        ))}
      </div>
      <div className="ph-transcript-text" ref={scrollRef} aria-live="polite">
        {empty ? (
          <p className="ph-transcript-empty">
            {listening ? 'Start speaking…' : 'Your words will appear here as you practice.'}
          </p>
        ) : (
          <p>
            {sentences.map((s, i) => (
              <span key={i}>{highlightFillers(s)} </span>
            ))}
            {partial && <span className="ph-partial">{partial}</span>}
          </p>
        )}
      </div>
    </section>
  )
}

// the counted filler words, plus the [um] / [uh] / [um?] markers Python inserts for detected fillers
const FILLER_PATTERN = /(\[[a-z?]+\]|\b(?:um|uh|like|you know|so)\b)/i

/** Highlights every filler the counter counted, so the transcript matches the numbers. */
function highlightFillers(sentence: string): React.ReactNode[] {
  return sentence.split(FILLER_PATTERN).map((part, i) =>
    i % 2 ? (
      <mark key={i} className="ph-filler-mark">
        {part.startsWith('[') ? part.slice(1, -1) : part}
      </mark>
    ) : (
      part
    )
  )
}
