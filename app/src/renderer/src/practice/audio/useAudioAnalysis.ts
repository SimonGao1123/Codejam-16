// Connection to the Python service's audio side, for as long as the practice page is open. While
// the session's microphone is on, its audio streams to Python, which sends back voice readings,
// the transcript with filler counts, and (if the user gave a script) where they are in the script.
// Used by Practice.tsx; the transcript, pitch graph and script cards just display what it returns.

import { useEffect, useRef, useState } from 'react'

import { VOICE_WINDOW_S, type VoiceSample } from '../components/VoiceGraph'
import { AudioAnalysisClient } from './audioAnalysisClient'
import { captureMic } from './audioCapture'
import {
  FILLER_WORDS,
  type FillerWord,
  type PaceSource,
  type PaceTargetMessage,
  type SessionScoreMessage,
  type ScriptPositionMessage,
  type TranscriptMessage,
  type VoiceMessage
} from './audioProtocol'

const NO_FILLERS = Object.fromEntries(FILLER_WORDS.map((w) => [w, 0])) as Record<FillerWord, number>
const FLASH_MS = 1500 // how long a filler chip stays highlighted after it's counted
const SCRIPT_DEBOUNCE_MS = 500 // send the script once typing pauses, not on every keystroke
const NOISE_BLOCK = 800 // 50 ms at 16 kHz, the service's voice block
const MIN_NOISE_BLOCKS = 10 // need at least 0.5 s of the countdown to trust the room level
const MAX_NOISE_BLOCKS = 60 // the 3 s countdown; waiting for calibration after it doesn't count

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

export interface AudioAnalysis {
  connected: boolean
  listening: boolean // mic on and connected
  sentences: string[]
  partial: string
  fillers: Record<FillerWord, number>
  total: number
  wpm: number | null
  wpmUpdatedAt: number | null
  flash: Partial<Record<FillerWord, number>> // fillers counted in the last FLASH_MS
  voice: VoiceMessage | null
  pitch: React.RefObject<VoiceSample[]>
  volume: React.RefObject<VoiceSample[]>
  wpmSamples: React.RefObject<VoiceSample[]>
  scriptChunks: string[] // the script as Python split it ([] = no script)
  scriptReady: boolean // comparison model loaded
  scriptPosition: ScriptPositionMessage | null
  avgWpm: number | null // last 3 finished lines, pauses included (null until there are 3)
  desiredWpm: number | null // the pace to aim for, from Python
  desiredSource: PaceSource | null
  paceTarget: PaceTargetMessage | null // latest reply to the script / time the user has
}

/** `script`: the user's script text; empty = no script (comparison off).
 *  `targetMinutes`: the time the user has to present; null = not set. */
export function useAudioAnalysis(
  micStream: MediaStream | null,
  script: string,
  targetMinutes: number | null,
  startedAt: number | null,
  onScore: (score: SessionScoreMessage | null) => void
): AudioAnalysis {
  const clientRef = useRef<AudioAnalysisClient | null>(null)
  const pitchRef = useRef<VoiceSample[]>([])
  const volumeRef = useRef<VoiceSample[]>([])
  const wpmRef = useRef<VoiceSample[]>([])
  const sessionNumber = useRef(0)
  const noiseLevels = useRef<number[]>([]) // dBFS per 50 ms block during the countdown
  const pendingScores = useRef<number[]>([])
  const [connected, setConnected] = useState(false)
  const [sentences, setSentences] = useState<string[]>([])
  const [partial, setPartial] = useState('')
  const [fillers, setFillers] = useState(NO_FILLERS)
  const [total, setTotal] = useState(0)
  const [wpm, setWpm] = useState<number | null>(null)
  const [wpmUpdatedAt, setWpmUpdatedAt] = useState<number | null>(null)
  const [flash, setFlash] = useState<Partial<Record<FillerWord, number>>>({})
  const [voice, setVoice] = useState<VoiceMessage | null>(null)
  const [scriptChunks, setScriptChunks] = useState<string[]>([])
  const [scriptReady, setScriptReady] = useState(false)
  const [scriptPosition, setScriptPosition] = useState<ScriptPositionMessage | null>(null)
  const [avgWpm, setAvgWpm] = useState<number | null>(null)
  const [desiredWpm, setDesiredWpm] = useState<number | null>(null)
  const [desiredSource, setDesiredSource] = useState<PaceSource | null>(null)
  const [paceTarget, setPaceTarget] = useState<PaceTargetMessage | null>(null)
  const targetSeconds = targetMinutes && targetMinutes > 0 ? targetMinutes * 60 : null
  const targetSecondsRef = useRef(targetSeconds)
  const scriptRef = useRef(script.trim() || null) // last script sent to Python
  const latestScriptRef = useRef(script.trim() || null) // latest typed, maybe not sent yet

  useEffect(() => {
    const client = new AudioAnalysisClient()
    clientRef.current = client
    client.setScript(scriptRef.current) // stored now, sent as soon as the socket opens
    client.setTargetTime(targetSecondsRef.current)
    client.onConnectionChange = (online) => {
      if (!online) pendingScores.current = []
      setConnected(online)
    }
    client.onMessage = (msg) => {
      if (msg.type === 'voice') {
        const now = performance.now() / 1000
        setVoice(msg)
        setWpm(msg.wpm ?? null) // a missing value means no reading, not a crash
        const wpmTime = msg.captured_at ?? now
        setWpmUpdatedAt(wpmTime)
        wpmRef.current.push({ t: wpmTime, value: msg.wpm })
        const samples = pitchRef.current
        // one reading per 50 ms block; the newest is "now"
        msg.pitches.forEach((value, i) =>
          samples.push({ t: now - (msg.pitches.length - 1 - i) * 0.05, value })
        )
        const volumeTime = msg.captured_at ?? now
        const volumes = volumeRef.current
        volumes.push({ t: volumeTime, value: msg.volume_dbfs })
        while (samples.length && samples[0].t < now - VOICE_WINDOW_S - 1) samples.shift()
        while (volumes.length && volumes[0].t < volumeTime - VOICE_WINDOW_S - 1) volumes.shift()
      } else if (msg.type === 'transcript') {
        applyTranscript(msg)
      } else if (msg.type === 'script_loaded') {
        setScriptChunks(msg.chunks)
        setScriptReady(msg.ready)
        setScriptPosition(null)
      } else if (msg.type === 'script_position') {
        setScriptPosition(msg)
      } else if (msg.type === 'pace_target') {
        setPaceTarget(msg)
        setDesiredWpm(msg.desired_wpm)
        setDesiredSource(msg.source)
      } else if (
        msg.type === 'session_score' &&
        pendingScores.current.shift() === sessionNumber.current
      ) {
        onScore(msg)
      }
    }
    function applyTranscript(msg: TranscriptMessage): void {
      if (msg.new_sentences.length) setSentences((s) => [...s, ...msg.new_sentences])
      setPartial(msg.partial)
      setFillers(msg.fillers)
      setTotal(msg.total)
      setWpm(msg.wpm ?? null) // a missing value means no reading, not a crash
      setAvgWpm(msg.avg_wpm ?? null)
      if (msg.desired_wpm !== undefined) setDesiredWpm(msg.desired_wpm)
      if (msg.desired_source !== undefined) setDesiredSource(msg.desired_source)
      if (msg.new_fillers.length) {
        const now = Date.now()
        setFlash((f) => ({ ...f, ...Object.fromEntries(msg.new_fillers.map((w) => [w, now])) }))
      }
    }
    return () => {
      client.close()
      clientRef.current = null
    }
  }, [onScore])

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

  // the time the user has: tiny message, sent on every change (and again after a reconnect)
  useEffect(() => {
    if (targetSeconds === targetSecondsRef.current) return
    targetSecondsRef.current = targetSeconds
    clientRef.current?.setTargetTime(targetSeconds)
  }, [targetSeconds])

  // Send session controls even when practicing with only a camera.
  useEffect(() => {
    const client = clientRef.current
    if (startedAt === null) return
    const number = ++sessionNumber.current
    onScore(null)
    if (!connected || !client) return
    setSentences([])
    setPartial('')
    setFillers(NO_FILLERS)
    setTotal(0)
    setWpm(null)
    setWpmUpdatedAt(null)
    setAvgWpm(null)
    setVoice(null)
    setScriptPosition(null)
    pitchRef.current = []
    volumeRef.current = []
    wpmRef.current = []
    if (latestScriptRef.current !== scriptRef.current) {
      // started within the debounce of an edit: send the script before the session begins
      scriptRef.current = latestScriptRef.current
      client.setScript(scriptRef.current)
    }
    const noise = noiseLevels.current
    if (noise.length >= MIN_NOISE_BLOCKS) client.send({ type: 'noise_floor', dbfs: median(noise) })
    client.send({ type: 'session_start', started_at: startedAt })
    return () => {
      if (!client.connected) return
      pendingScores.current.push(number)
      client.send({ type: 'session_end' })
    }
  }, [startedAt, connected, onScore])

  useEffect(() => {
    const client = clientRef.current
    if (startedAt === null || !connected || !micStream || !client) return
    let stopped = false
    let capture: { stop(): void } | null = null
    captureMic(micStream, (samples, chunkStart) => client.sendAudio(samples, chunkStart)).then(
      (c) => (stopped ? c.stop() : (capture = c)),
      (err) => console.error('microphone capture failed', err)
    )
    return () => {
      stopped = true
      capture?.stop()
    }
  }, [startedAt, connected, micStream])

  // get-ready countdown (mic open, session not started yet): the user is standing still and silent,
  // so measure the room's noise level. Same 16 kHz audio and dBFS formula as the service's voice
  // monitor; sent just before session_start.
  useEffect(() => {
    if (!micStream || startedAt !== null) return
    noiseLevels.current = []
    let stopped = false
    let capture: { stop(): void } | null = null
    let pending = new Float32Array(0)
    captureMic(micStream, (samples) => {
      if (noiseLevels.current.length >= MAX_NOISE_BLOCKS) return
      const joined = new Float32Array(pending.length + samples.length)
      joined.set(pending)
      joined.set(samples, pending.length)
      let i = 0
      for (; i + NOISE_BLOCK <= joined.length; i += NOISE_BLOCK) {
        let sum = 0
        for (let j = i; j < i + NOISE_BLOCK; j++) sum += joined[j] * joined[j]
        noiseLevels.current.push(20 * Math.log10(Math.max(Math.sqrt(sum / NOISE_BLOCK), 1e-8)))
      }
      pending = joined.slice(i)
    }).then(
      (c) => (stopped ? c.stop() : (capture = c)),
      (err) => console.error('microphone capture failed', err)
    )
    return () => {
      stopped = true
      capture?.stop()
    }
  }, [micStream, startedAt])

  // un-highlight filler chips after FLASH_MS
  useEffect(() => {
    if (!Object.keys(flash).length) return
    const timer = window.setTimeout(() => setFlash({}), FLASH_MS)
    return () => window.clearTimeout(timer)
  }, [flash])

  return {
    connected,
    listening: startedAt !== null && Boolean(micStream) && connected,
    sentences,
    partial,
    fillers,
    total,
    wpm,
    wpmUpdatedAt,
    flash,
    voice,
    pitch: pitchRef,
    volume: volumeRef,
    wpmSamples: wpmRef,
    scriptChunks,
    scriptReady,
    scriptPosition,
    avgWpm,
    desiredWpm,
    desiredSource,
    paceTarget
  }
}
