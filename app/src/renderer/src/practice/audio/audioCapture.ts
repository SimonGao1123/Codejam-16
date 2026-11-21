// Turns the practice session's microphone stream into 16 kHz mono float32 chunks
// (the worklet does the downsampling from the mic's native rate).

import workletUrl from './audioCaptureWorklet.ts?worker&url'

export interface MicCapture {
  stop(): void
}

/** Emits ~128 ms of audio with the chunk start on the renderer's monotonic clock. */
export async function captureMic(
  stream: MediaStream,
  onChunk: (samples: Float32Array, startedAt: number) => void
): Promise<MicCapture> {
  const context = new AudioContext() // the mic's native rate, usually 48 kHz
  await context.audioWorklet.addModule(workletUrl)
  const source = context.createMediaStreamSource(stream)
  const chunker = new AudioWorkletNode(context, 'mic-chunker', {
    numberOfInputs: 1,
    numberOfOutputs: 0,
    channelCount: 1,
    channelCountMode: 'explicit' // mix stereo mics down to mono
  })
  let contextOrigin: number | null = null
  chunker.port.onmessage = (e: MessageEvent<{ samples: Float32Array; startTime: number }>) => {
    contextOrigin ??= performance.now() / 1000 - context.currentTime
    onChunk(e.data.samples, contextOrigin + e.data.startTime)
  }
  source.connect(chunker)
  return {
    stop() {
      source.disconnect()
      chunker.port.onmessage = null
      void context.close()
    }
  }
}
