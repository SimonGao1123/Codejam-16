import { useEffect, useState } from 'react'

type Props = { micStream: MediaStream | null; pending: boolean }

export default function MicrophoneMeter({ micStream, pending }: Props): React.JSX.Element {
  const [level, setLevel] = useState(0)
  useEffect(() => {
    if (!micStream) return
    const context = new AudioContext()
    const source = context.createMediaStreamSource(micStream)
    const analyser = context.createAnalyser()
    analyser.fftSize = 1024
    source.connect(analyser)
    const buffer = new Float32Array(analyser.fftSize)
    const timer = window.setInterval(() => {
      analyser.getFloatTimeDomainData(buffer)
      const amplitude = Math.sqrt(
        buffer.reduce((sum, sample) => sum + sample * sample, 0) / buffer.length
      )
      const dbfs = 20 * Math.log10(Math.max(amplitude, 1e-8))
      setLevel(Math.round(Math.min(100, Math.max(0, (dbfs + 60) * (100 / 60)))))
    }, 80)
    return () => {
      window.clearInterval(timer)
      source.disconnect()
      void context.close()
    }
  }, [micStream])

  return (
    <div className="ph-audio">
      <div>
        <strong>Microphone level</strong>{' '}
        <span>{micStream ? 'Mic on' : pending ? 'Connecting' : 'Mic off'}</span>
      </div>
      <meter aria-label="Microphone input level" min={0} max={100} value={micStream ? level : 0} />
    </div>
  )
}
