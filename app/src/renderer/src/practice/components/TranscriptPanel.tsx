// Voice pitch graph + running transcript with the filler word counter, below the camera preview.
// Shows what useAudioAnalysis (audio/useAudioAnalysis.ts) receives from the Python service.

import { useEffect, useRef } from 'react'

import type { AudioAnalysis } from '../audio/useAudioAnalysis'
import { FILLER_WORDS } from '../audio/audioProtocol'
import VoiceGraph from './VoiceGraph'

type Props = {
  speech: AudioAnalysis
  running: boolean
}

export default function TranscriptPanel({ speech, running }: Props): React.JSX.Element {
  const { connected, listening, sentences, partial, fillers, total, flash, voice } = speech
  const scrollRef = useRef<HTMLDivElement>(null)

  // keep the newest words in view
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [sentences, partial])

  const empty = !sentences.length && !partial

  return (
    <>
      <section className="ph-pitch" aria-labelledby="ph-pitch-heading">
        <div className="ph-card-head">
          <h2 id="ph-pitch-heading">Voice pitch</h2>
          <span>
            {listening && voice
              ? voice.pitch_hz
                ? `${voice.pitch_hz.toFixed(0)} Hz`
                : 'Not speaking'
              : listening
                ? 'Waiting for audio'
                : 'Starts with your practice session'}
          </span>
        </div>
        <VoiceGraph
          metric="pitch"
          samples={speech.pitch}
          typicalHz={voice?.typical_hz ?? null}
          spikeHz={voice?.spike_hz ?? null}
          calibrated={voice?.calibrated ?? false}
          live={listening}
        />
      </section>
      <section className="ph-pitch" aria-labelledby="ph-volume-heading">
        <div className="ph-card-head">
          <h2 id="ph-volume-heading">Microphone volume</h2>
          {voice && <span>{voice.volume_dbfs.toFixed(0)} dBFS</span>}
        </div>
        <VoiceGraph metric="volume" samples={speech.volume} live={listening} />
      </section>
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
    </>
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
