import { useEffect, useRef, useState } from 'react'
import { useAlertPreferences } from '../liveAlert/useAlertPreferences'
import {
  FILLER_WORDS,
  type AudioServerMessage,
  type AudioChunkProcessedMessage,
  type FillerWord,
  type SessionScoreMessage,
  type TranscriptMessage,
  type VoiceMessage
} from '../practice/audio/audioProtocol'
import { createLandmarkers, detect, type Landmarkers } from '../practice/camera/webcamLandmarkers'
import type { VoiceSample } from '../practice/components/VoiceGraph'
import { pdfText } from '../practice/script/pdfText'
import SessionResults from '../practice/components/SessionResults'
import PresentationPlayback from '../practice/components/PresentationPlayback'
import TranscriptPanel, { type TranscriptAnalysis } from '../practice/components/TranscriptPanel'
import './upload.css'

const EMPTY_FILLERS = Object.fromEntries(FILLER_WORDS.map((word) => [word, 0])) as Record<
  FillerWord,
  number
>
const GRAPH_WINDOW_S = 16

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function UploadGrade(): React.JSX.Element {
  const [preferences] = useAlertPreferences()
  const [file, setFile] = useState<File | null>(null)
  const [fileUrl, setFileUrl] = useState<string | null>(null)
  const [mediaDuration, setMediaDuration] = useState<number>(0)
  const [isVideo, setIsVideo] = useState(false)
  const [script, setScript] = useState('')
  const [targetMinutes, setTargetMinutes] = useState<number | null>(null)
  const [pdfStatus, setPdfStatus] = useState<string | null>(null)

  const [processing, setProcessing] = useState(false)
  const [progress, setProgress] = useState(0)
  const [statusText, setStatusText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [score, setScore] = useState<SessionScoreMessage | null>(null)
  const [analysisConnected, setAnalysisConnected] = useState(false)
  const [sentences, setSentences] = useState<string[]>([])
  const [partial, setPartial] = useState('')
  const [fillers, setFillers] = useState<Record<FillerWord, number>>(EMPTY_FILLERS)
  const [fillerTotal, setFillerTotal] = useState(0)
  const [voice, setVoice] = useState<VoiceMessage | null>(null)
  const [wpm, setWpm] = useState<number | null>(null)
  const [averageWpm, setAverageWpm] = useState<number | null>(null)
  const [desiredWpm, setDesiredWpm] = useState<number | null>(null)

  const videoRef = useRef<HTMLVideoElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const pdfInputRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef(false)
  const pitchSamples = useRef<VoiceSample[]>([])
  const volumeSamples = useRef<VoiceSample[]>([])
  const wpmSamples = useRef<VoiceSample[]>([])

  const uploadSpeech: TranscriptAnalysis = {
    connected: analysisConnected,
    listening: processing,
    sentences,
    partial,
    fillers,
    total: fillerTotal,
    flash: {},
    voice,
    pitch: pitchSamples,
    volume: volumeSamples,
    wpmSamples,
    wpm,
    avgWpm: averageWpm,
    desiredWpm
  }

  const resetAudioResults = (): void => {
    setAnalysisConnected(false)
    setSentences([])
    setPartial('')
    setFillers(EMPTY_FILLERS)
    setFillerTotal(0)
    setVoice(null)
    setWpm(null)
    setAverageWpm(null)
    setDesiredWpm(null)
    pitchSamples.current = []
    volumeSamples.current = []
    wpmSamples.current = []
  }

  const applyVoice = (message: VoiceMessage): void => {
    setVoice(message)
    const capturedAt = message.captured_at
    if (capturedAt == null) return
    setWpm(message.wpm)
    message.pitches.forEach((value, index) => {
      pitchSamples.current.push({
        t: message.pitch_times?.[index] ?? capturedAt - (message.pitches.length - 1 - index) * 0.05,
        value
      })
    })
    volumeSamples.current.push({ t: capturedAt, value: message.volume_dbfs })
    wpmSamples.current.push({ t: capturedAt, value: message.wpm })
    for (const samples of [pitchSamples.current, volumeSamples.current]) {
      while (samples.length && samples[0].t < capturedAt - GRAPH_WINDOW_S) samples.shift()
    }
  }

  const applyTranscript = (message: TranscriptMessage): void => {
    if (message.new_sentences.length)
      setSentences((current) => [...current, ...message.new_sentences])
    setPartial(message.partial)
    setFillers(message.fillers)
    setFillerTotal(message.total)
    setWpm(message.wpm)
    setAverageWpm(message.avg_wpm ?? null)
    if (message.desired_wpm !== undefined) setDesiredWpm(message.desired_wpm)
  }

  // Clean up object URL when component unmounts or file changes
  useEffect(() => {
    return () => {
      if (!fileUrl) return
      if (fileUrl.startsWith('blob:')) URL.revokeObjectURL(fileUrl)
      else if (fileUrl.startsWith('uploaded-media:')) void window.api?.uploadMedia.release(fileUrl)
    }
  }, [fileUrl])

  const handleFile = async (selected: File): Promise<void> => {
    if (processing) return
    setError(null)
    setScore(null)
    setProgress(0)
    setStatusText('')
    resetAudioResults()

    // Use the range-capable Electron protocol used by live recordings. The blob fallback keeps the
    // upload page usable in a plain browser during development.
    let url: string
    try {
      url = await window.api.uploadMedia.register(selected)
    } catch (mediaError) {
      console.warn(
        'Could not register uploaded media for streaming; using a blob URL instead.',
        mediaError
      )
      url = URL.createObjectURL(selected)
    }
    setFile(selected)
    setFileUrl(url)

    const isVid = selected.type.startsWith('video/') || /\.(mp4|webm|mov|mkv)$/i.test(selected.name)
    setIsVideo(isVid)

    // Probe duration safely
    try {
      if (isVid) {
        const vid = document.createElement('video')
        vid.src = url
        vid.preload = 'metadata'
        if (vid.readyState >= 1) {
          const d = vid.duration
          if (Number.isFinite(d) && d > 0) setMediaDuration(d)
        } else {
          await new Promise<void>((resolve) => {
            const timer = setTimeout(resolve, 1500)
            vid.onloadedmetadata = (): void => {
              clearTimeout(timer)
              const d = vid.duration
              if (Number.isFinite(d) && d > 0) setMediaDuration(d)
              resolve()
            }
          })
        }
      } else {
        const audio = document.createElement('audio')
        audio.src = url
        audio.preload = 'metadata'
        if (audio.readyState >= 1) {
          const d = audio.duration
          if (Number.isFinite(d) && d > 0) setMediaDuration(d)
        } else {
          await new Promise<void>((resolve) => {
            const timer = setTimeout(resolve, 1500)
            audio.onloadedmetadata = (): void => {
              clearTimeout(timer)
              const d = audio.duration
              if (Number.isFinite(d) && d > 0) setMediaDuration(d)
              resolve()
            }
          })
        }
      }
    } catch (err) {
      console.warn('Metadata probe warning:', err)
    }
  }

  const handleDrop = (e: React.DragEvent): void => {
    e.preventDefault()
    if (e.dataTransfer.files?.[0]) {
      void handleFile(e.dataTransfer.files[0])
    }
  }

  const handlePdfAttach = async (e: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const f = e.target.files?.[0]
    if (!f) return
    setPdfStatus(`Reading ${f.name}…`)
    try {
      const text = await pdfText(f)
      setScript(text)
      setPdfStatus(`Loaded from ${f.name}`)
    } catch (err) {
      setPdfStatus(err instanceof Error ? err.message : 'Could not read PDF')
    } finally {
      if (pdfInputRef.current) pdfInputRef.current.value = ''
    }
  }

  const processRecording = async (): Promise<void> => {
    if (!file || !fileUrl || processing) return
    setProcessing(true)
    setError(null)
    setScore(null)
    resetAudioResults()
    setProgress(0)
    setStatusText('Preparing file...')
    abortRef.current = false

    const servicePort = window.api?.servicePort || 8766
    let landmarkers: Landmarkers | null = null
    let audioWs: WebSocket | null = null
    let webcamWs: WebSocket | null = null
    let audioCtx: AudioContext | null = null
    const audioAckWaiters = new Map<
      number,
      { resolve: () => void; reject: (reason: Error) => void; timer: ReturnType<typeof setTimeout> }
    >()

    try {
      // 1. Decode audio track to 16 kHz mono Float32 PCM
      setStatusText('Decoding audio track...')
      let pcm = new Float32Array(0)
      let audioDuration = mediaDuration

      try {
        const arrayBuffer = await file.arrayBuffer()
        const AudioCtxClass =
          window.AudioContext ||
          (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
        audioCtx = new AudioCtxClass({ sampleRate: 16000 })
        const decodedBuffer = await audioCtx.decodeAudioData(arrayBuffer)
        audioDuration = decodedBuffer.duration || mediaDuration || 1

        if (decodedBuffer.numberOfChannels === 1) {
          pcm = decodedBuffer.getChannelData(0)
        } else {
          const ch0 = decodedBuffer.getChannelData(0)
          const ch1 = decodedBuffer.getChannelData(1)
          pcm = new Float32Array(decodedBuffer.length)
          for (let i = 0; i < decodedBuffer.length; i++) {
            pcm[i] = (ch0[i] + ch1[i]) / 2
          }
        }
      } catch (audioErr) {
        console.warn('Audio decoding warning (continuing with video-only if needed):', audioErr)
      }

      // 2. Open WebSockets with safety timeouts
      setStatusText('Connecting to analysis service...')
      audioWs = new WebSocket(`ws://127.0.0.1:${servicePort}/audio`)
      audioWs.binaryType = 'arraybuffer'
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => {
          reject(
            new Error(
              `Could not connect to Python analysis service on port ${servicePort}. Make sure the service is running.`
            )
          )
        }, 4000)
        audioWs!.onopen = (): void => {
          clearTimeout(timer)
          resolve()
        }
        audioWs!.onerror = (): void => {
          clearTimeout(timer)
          reject(new Error(`Connection to audio service failed on port ${servicePort}`))
        }
      })

      // Send alert settings immediately to audio service
      audioWs.send(JSON.stringify({ type: 'settings', rules: preferences.rules }))

      const vid = videoRef.current
      if (isVideo) {
        setStatusText('Loading vision models for posture and hand tracking...')
        try {
          landmarkers = await createLandmarkers()
        } catch (lmErr) {
          throw new Error(`Failed to load vision tracking models: ${lmErr}`, { cause: lmErr })
        }

        webcamWs = new WebSocket(`ws://127.0.0.1:${servicePort}/webcam`)
        await new Promise<void>((resolve, reject) => {
          const timer = setTimeout(() => {
            reject(new Error(`Could not connect to Python webcam service on port ${servicePort}`))
          }, 4000)
          webcamWs!.onopen = (): void => {
            clearTimeout(timer)
            webcamWs?.send(JSON.stringify({ type: 'settings', rules: preferences.rules }))
            resolve()
          }
          webcamWs!.onerror = (): void => {
            clearTimeout(timer)
            reject(new Error(`Connection to webcam service failed on port ${servicePort}`))
          }
        })

        // Ensure video is ready to seek
        if (vid && vid.readyState < 2) {
          await new Promise<void>((resolve) => {
            const timer = setTimeout(resolve, 2000)
            const onReady = (): void => {
              clearTimeout(timer)
              vid.removeEventListener('loadeddata', onReady)
              vid.removeEventListener('canplay', onReady)
              resolve()
            }
            vid.addEventListener('loadeddata', onReady)
            vid.addEventListener('canplay', onReady)
          })
        }
      }

      // 3. Set up session control messages
      const scorePromise = new Promise<SessionScoreMessage>((resolve, reject) => {
        if (!audioWs) return reject(new Error('No audio socket'))
        audioWs.onmessage = (event): void => {
          try {
            const msg = JSON.parse(event.data) as AudioServerMessage | AudioChunkProcessedMessage
            if (msg.type === 'voice') applyVoice(msg)
            else if (msg.type === 'transcript') applyTranscript(msg)
            else if (msg.type === 'pace_target') setDesiredWpm(msg.desired_wpm)
            else if (msg.type === 'audio_processed') {
              const waiter = audioAckWaiters.get(msg.id)
              if (waiter) {
                clearTimeout(waiter.timer)
                audioAckWaiters.delete(msg.id)
                waiter.resolve()
              }
            } else if (msg.type === 'session_score') {
              resolve(msg)
            }
          } catch (e) {
            console.warn('Error parsing message from service:', e)
          }
        }
      })

      audioWs.send(JSON.stringify({ type: 'script', text: script.trim() || null }))
      audioWs.send(
        JSON.stringify({
          type: 'target_time',
          seconds: targetMinutes && targetMinutes > 0 ? targetMinutes * 60 : null
        })
      )
      // Uploaded recordings establish their own pitch and volume baseline. Their codec gain and
      // microphone setup should not be compared with a saved live-microphone calibration.
      audioWs.send(JSON.stringify({ type: 'noise_floor', dbfs: null }))
      audioWs.send(
        JSON.stringify({
          type: 'session_start',
          started_at: 0.0,
          voice_baseline: null,
          recording_baseline: true
        })
      )
      setAnalysisConnected(true)

      // 4. Stream audio and video frames in stepped slices
      const duration = Math.max(
        audioDuration,
        vid?.duration && Number.isFinite(vid.duration) ? vid.duration : 0,
        1
      )
      setMediaDuration(duration)
      const stepSeconds = 0.2 // 5 fps (fast and accurate for hand gestures and eye level)
      const samplesPerStep = Math.round(stepSeconds * 16000)
      const totalSteps = Math.ceil(duration / stepSeconds)
      let nextAudioChunkId = 0

      const waitForAudioProcessed = (id: number): Promise<void> =>
        new Promise((resolve, reject) => {
          const timer = setTimeout(() => {
            audioAckWaiters.delete(id)
            reject(new Error('Timed out waiting for the transcription service to process audio'))
          }, 15_000)
          audioAckWaiters.set(id, { resolve, reject, timer })
        })

      const seekVideo = (videoEl: HTMLVideoElement, targetTime: number): Promise<void> =>
        new Promise((resolve, reject) => {
          if (Math.abs(videoEl.currentTime - targetTime) < 0.04) {
            resolve()
            return
          }
          let resolved = false
          const done = (): void => {
            if (!resolved) {
              resolved = true
              videoEl.removeEventListener('seeked', done)
              clearTimeout(timer)
              if (Math.abs(videoEl.currentTime - targetTime) < 0.04) resolve()
              else
                reject(
                  new Error(`Could not seek to ${formatTime(targetTime)} in the uploaded video.`)
                )
            }
          }
          const timer = setTimeout(() => {
            if (!resolved) {
              resolved = true
              videoEl.removeEventListener('seeked', done)
              reject(
                new Error(`Timed out seeking to ${formatTime(targetTime)} in the uploaded video.`)
              )
            }
          }, 5000)
          videoEl.addEventListener('seeked', done)
          videoEl.currentTime = targetTime
        })

      for (let step = 0; step < totalSteps; step++) {
        if (abortRef.current) throw new Error('Analysis stopped by user')
        const t = step * stepSeconds
        if (t >= duration) break

        // A. Process Video Frame if video
        if (isVideo && vid && landmarkers && webcamWs?.readyState === WebSocket.OPEN) {
          await seekVideo(vid, t)
          try {
            const frameMsg = detect(landmarkers, vid, Math.max(0.001, t))
            webcamWs.send(JSON.stringify(frameMsg))
          } catch (detErr) {
            console.warn('Frame detection warning at', t, detErr)
          }
        }

        // B. Send Audio Chunk
        if (pcm.length > 0 && audioWs.readyState === WebSocket.OPEN) {
          const startSample = step * samplesPerStep
          const endSample = Math.min(startSample + samplesPerStep, pcm.length)
          if (startSample < pcm.length) {
            const chunk = pcm.subarray(startSample, endSample)
            const buffer = new ArrayBuffer(Float64Array.BYTES_PER_ELEMENT + chunk.byteLength)
            new DataView(buffer).setFloat64(0, t, true)
            new Float32Array(buffer, Float64Array.BYTES_PER_ELEMENT).set(chunk)
            const chunkId = nextAudioChunkId++
            audioWs.send(buffer)
            // Do not let a fast file upload queue minutes of audio ahead of the streaming model.
            // The service replies only after it has processed this ordered binary chunk.
            audioWs.send(JSON.stringify({ type: 'audio_sync', id: chunkId }))
            await waitForAudioProcessed(chunkId)
          }
        }

        // C. Flow control & UI progress
        const pct = Math.min(99, Math.round(((step + 1) / totalSteps) * 100))
        setProgress(pct)
        setStatusText(
          `Analyzing presentation... ${pct}% (${formatTime(t)} / ${formatTime(duration)})`
        )

        // Yield so progress and the video preview can paint between steps.
        await new Promise((resolve) => setTimeout(resolve, 0))
      }

      // Reset the preview video so it releases active decoding and returns to t=0
      if (vid) {
        vid.pause()
        vid.currentTime = 0
      }

      // 5. Finalize Session
      const webcamSocket = webcamWs
      if (webcamSocket?.readyState === WebSocket.OPEN) {
        setStatusText('Finishing body-language analysis...')
        await new Promise<void>((resolve, reject) => {
          const timer = setTimeout(() => {
            webcamSocket.removeEventListener('message', onMessage)
            reject(new Error('Timed out finishing body-language analysis'))
          }, 30000)
          const onMessage = (event: MessageEvent): void => {
            try {
              if (JSON.parse(event.data).type !== 'flushed') return
              clearTimeout(timer)
              webcamSocket.removeEventListener('message', onMessage)
              resolve()
            } catch {
              // Overlay messages are expected before the flush acknowledgement.
            }
          }
          webcamSocket.addEventListener('message', onMessage)
          webcamSocket.send(JSON.stringify({ type: 'flush' }))
        })
      }

      setStatusText('Computing score and grading delivery...')
      if (audioWs.readyState === WebSocket.OPEN) {
        audioWs.send(JSON.stringify({ type: 'session_end', duration_seconds: duration }))
      }

      // Transcription and script comparison finish after the final audio chunk, so use the same
      // generous result timeout as a live practice session.
      const finalScore = await Promise.race([
        scorePromise,
        new Promise<SessionScoreMessage>((_, reject) =>
          setTimeout(() => reject(new Error('Timed out waiting for session score')), 30000)
        )
      ])

      setScore(finalScore)
      setMediaDuration(duration)
      setProgress(100)
      setStatusText('Grading complete!')
    } catch (err) {
      console.error('Processing error:', err)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      for (const waiter of audioAckWaiters.values()) {
        clearTimeout(waiter.timer)
        waiter.reject(new Error('Audio analysis stopped'))
      }
      audioAckWaiters.clear()
      landmarkers?.close()
      webcamWs?.close()
      audioWs?.close()
      void audioCtx?.close()
      setProcessing(false)
    }
  }

  return (
    <main className="ph-practice">
      <header className="ph-heading">
        <div>
          <h1>Upload & Grade</h1>
          <p>
            Upload a presentation recording and get a delivery score, filler word counts, hand
            movement feedback, eye level check, and timeline checkpoints.
          </p>
        </div>
        <span className="ph-session-state" data-active={processing}>
          {processing ? 'Analyzing recording' : score ? 'Grading complete' : 'Ready'}
        </span>
      </header>

      <div className="ph-upload-section">
        {/* Upload Card */}
        <section className="ph-upload-card" aria-labelledby="ph-upload-file-heading">
          <div className="ph-card-head">
            <h2 id="ph-upload-file-heading">1. Select Recording</h2>
            <span>Video (.mp4, .webm, .mov) or Audio (.wav, .mp3, .m4a)</span>
          </div>

          {!file ? (
            <div
              className="ph-dropzone"
              onDragOver={(e): void => e.preventDefault()}
              onDrop={handleDrop}
              onClick={(): void => fileInputRef.current?.click()}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept="video/*,audio/*,.mp4,.webm,.mov,.mkv,.wav,.mp3,.m4a"
                style={{ display: 'none' }}
                onChange={(e): void => {
                  if (e.target.files?.[0]) void handleFile(e.target.files[0])
                }}
              />
              <span className="ph-dropzone-icon" aria-hidden="true">
                📁
              </span>
              <p className="ph-dropzone-text">Click or drag a presentation file here</p>
              <p className="ph-dropzone-sub">Supports MP4, WebM, MOV, WAV, MP3, M4A</p>
            </div>
          ) : (
            <div>
              <div className="ph-file-preview">
                <div className="ph-file-meta">
                  <span className="ph-file-name">{file.name}</span>
                  <span className="ph-file-details">
                    {formatBytes(file.size)}
                    {mediaDuration > 0 && ` · Duration: ${formatTime(mediaDuration)}`}
                    {isVideo ? ' · Video with Hand & Eye Tracking' : ' · Audio Only'}
                  </span>
                </div>
                <button
                  type="button"
                  className="ph-change-file-btn"
                  disabled={processing}
                  onClick={(): void => {
                    setFile(null)
                    setFileUrl(null)
                    setScore(null)
                  }}
                >
                  Choose Different File
                </button>
              </div>

              {isVideo && fileUrl && (
                <video
                  ref={videoRef}
                  src={fileUrl}
                  controls
                  playsInline
                  muted
                  className="ph-upload-video-preview"
                  style={{ display: score ? 'none' : 'block' }}
                  onLoadedMetadata={(e): void => {
                    const d = e.currentTarget.duration
                    if (Number.isFinite(d) && d > 0) setMediaDuration(d)
                  }}
                />
              )}
            </div>
          )}
        </section>

        {/* Script & Target Time Card */}
        <section className="ph-upload-card" aria-labelledby="ph-upload-script-heading">
          <div className="ph-card-head">
            <h2 id="ph-upload-script-heading">2. Script & Expected Time (Optional)</h2>
            <span>{script.trim() ? 'Script attached' : 'Optional · Script tracking'}</span>
          </div>

          <div className="ph-script-edit">
            <textarea
              className="ph-script-input"
              rows={5}
              placeholder="Paste your presentation script here, or attach a text PDF below (optional)..."
              value={script}
              disabled={processing}
              onChange={(e): void => setScript(e.target.value)}
            />
            <div className="ph-script-actions" style={{ marginTop: '10px' }}>
              <input
                ref={pdfInputRef}
                type="file"
                accept="application/pdf"
                style={{ display: 'none' }}
                onChange={handlePdfAttach}
              />
              <button
                type="button"
                className="ph-change-file-btn"
                disabled={processing}
                onClick={(): void => pdfInputRef.current?.click()}
              >
                Attach PDF script
              </button>
              {pdfStatus && (
                <span style={{ fontSize: '13px', color: '#c4bfda', marginLeft: '12px' }}>
                  {pdfStatus}
                </span>
              )}
            </div>

            <div style={{ marginTop: '14px', display: 'flex', alignItems: 'center', gap: '10px' }}>
              <label htmlFor="target-min" style={{ fontSize: '14px', color: '#c4bfda' }}>
                Time limit / Target duration (minutes):
              </label>
              <input
                id="target-min"
                type="number"
                min="0.5"
                step="0.5"
                max="60"
                style={{
                  width: '90px',
                  padding: '6px 10px',
                  background: '#171825',
                  border: '1px solid #3d3755',
                  borderRadius: '6px',
                  color: '#fff'
                }}
                disabled={processing}
                value={targetMinutes ?? ''}
                onChange={(e): void => {
                  const val = parseFloat(e.target.value)
                  setTargetMinutes(val > 0 ? val : null)
                }}
                placeholder="e.g. 5"
              />
            </div>
          </div>
        </section>

        {/* Process Button & Progress */}
        <section className="ph-upload-card" style={{ textAlign: 'center' }}>
          <button
            type="button"
            className="ph-btn-process"
            disabled={!file || processing}
            onClick={(): void => void processRecording()}
          >
            {processing && <span className="ph-spinner" aria-hidden="true" />}
            {processing ? 'Processing...' : 'Grade Presentation'}
          </button>

          {processing && (
            <div style={{ maxWidth: '480px', margin: '20px auto 0' }}>
              <div className="ph-process-bar">
                <div className="ph-process-bar-fill" style={{ width: `${progress}%` }} />
              </div>
              <div className="ph-process-status">
                <span>{statusText}</span>
                <span>{progress}%</span>
              </div>
            </div>
          )}

          {error && (
            <p className="ph-error" role="status" style={{ marginTop: '16px' }}>
              {error}
            </p>
          )}
        </section>
      </div>

      {/* Results Section */}
      {score && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <SessionResults result={score} />

          {fileUrl && (
            <PresentationPlayback
              url={fileUrl}
              visible
              isVideo={isVideo}
              recordedDuration={
                mediaDuration > 0
                  ? mediaDuration
                  : (score as { duration_minutes?: number }).duration_minutes
                    ? (score as { duration_minutes?: number }).duration_minutes! * 60
                    : 0
              }
              checkpoints={score.checkpoints ?? []}
            />
          )}
          <TranscriptPanel
            speech={uploadSpeech}
            running={false}
            completedLabel="Uploaded recording"
          />
        </div>
      )}
    </main>
  )
}
