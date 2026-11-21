// Connection to the Python service's audio side, for as long as the practice page is open. While
// the session's microphone is on, its audio streams to Python, which sends back voice readings,
// the transcript with filler counts, and (if the user gave a script) where they are in the script.
// Used by Practice.tsx; the transcript, pitch graph and script cards just display what it returns.

import { useEffect, useRef, useState } from 'react'

import type { PitchSample } from '../components/PitchGraph'
import { PITCH_WINDOW_S } from '../components/PitchGraph'
import { AudioAnalysisClient } from './audioAnalysisClient'
import { captureMic } from './audioCapture'
import {
  FILLER_WORDS,
  type FillerWord,
  type ScriptPositionMessage,
  type TranscriptMessage,
  type VoiceMessage
} from './audioProtocol'

const NO_FILLERS = Object.fromEntries(FILLER_WORDS.map((w) => [w, 0])) as Record<FillerWord, number>
const FLASH_MS = 1500 // how long a filler chip stays highlighted after it's counted
const SCRIPT_DEBOUNCE_MS = 500 // send the script once typing pauses, not on every keystroke

export interface AudioAnalysis {
  connected: boolean
  listening: boolean // mic on and connected
  sentences: string[]
  partial: string
  fillers: Record<FillerWord, number>
  total: number
  flash: Partial<Record<FillerWord, number>> // fillers counted in the last FLASH_MS
  voice: VoiceMessage | null
  pitch: React.RefObject<PitchSample[]> // last PITCH_WINDOW_S of readings, for the graph
  scriptChunks: string[] // the script as Python split it ([] = no script)
  scriptReady: boolean // comparison model loaded
  scriptPosition: ScriptPositionMessage | null
}

/** `script`: the user's script text; empty = no script (comparison off). */
export function useAudioAnalysis(micStream: MediaStream | null, script: string): AudioAnalysis {
  const clientRef = useRef<AudioAnalysisClient | null>(null)
  const pitchRef = useRef<PitchSample[]>([])
  const [connected, setConnected] = useState(false)
  const [sentences, setSentences] = useState<string[]>([])
  const [partial, setPartial] = useState('')
  const [fillers, setFillers] = useState(NO_FILLERS)
  const [total, setTotal] = useState(0)
  const [flash, setFlash] = useState<Partial<Record<FillerWord, number>>>({})
  const [voice, setVoice] = useState<VoiceMessage | null>(null)
  const [scriptChunks, setScriptChunks] = useState<string[]>([])
  const [scriptReady, setScriptReady] = useState(false)
  const [scriptPosition, setScriptPosition] = useState<ScriptPositionMessage | null>(null)
  const scriptRef = useRef(script.trim() || null) // last script sent to Python
  const latestScriptRef = useRef(script.trim() || null) // latest typed, maybe not sent yet

  useEffect(() => {
    const client = new AudioAnalysisClient()
    clientRef.current = client
    client.setScript(scriptRef.current) // stored now, sent as soon as the socket opens
    client.onConnectionChange = setConnected
    client.onMessage = (msg) => {
      if (msg.type === 'voice') {
        setVoice(msg)
        const now = performance.now() / 1000
        const samples = pitchRef.current
        // one reading per 50 ms block; the newest is "now"
        msg.pitches.forEach((hz, i) =>
          samples.push({ t: now - (msg.pitches.length - 1 - i) * 0.05, hz })
        )
        while (samples.length && samples[0].t < now - PITCH_WINDOW_S - 1) samples.shift()
      } else if (msg.type === 'transcript') {
        applyTranscript(msg)
      } else if (msg.type === 'script_loaded') {
        setScriptChunks(msg.chunks)
        setScriptReady(msg.ready)
        setScriptPosition(null)
      } else if (msg.type === 'script_position') {
        setScriptPosition(msg)
      }
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

  // the script: sent once typing pauses (and again after a reconnect, by the client)
  useEffect(() => {
    const text = script.trim() || null
    latestScriptRef.current = text
    if (text === scriptRef.current) return
    const timer = window.setTimeout(() => {
      scriptRef.current = text
      clientRef.current?.setScript(text)
      if (!text) {
        setScriptChunks([])
        setScriptPosition(null)
      }
    }, SCRIPT_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [script])

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
    setScriptPosition(null)
    pitchRef.current = []
    if (latestScriptRef.current !== scriptRef.current) {
      // started within the debounce of an edit: send the script before the session begins
      scriptRef.current = latestScriptRef.current
      client.setScript(scriptRef.current)
    }
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

  // un-highlight filler chips after FLASH_MS
  useEffect(() => {
    if (!Object.keys(flash).length) return
    const timer = window.setTimeout(() => setFlash({}), FLASH_MS)
    return () => window.clearTimeout(timer)
  }, [flash])

  return {
    connected,
    listening: Boolean(micStream) && connected,
    sentences,
    partial,
    fillers,
    total,
    flash,
    voice,
    pitch: pitchRef,
    scriptChunks,
    scriptReady,
    scriptPosition
  }
}
