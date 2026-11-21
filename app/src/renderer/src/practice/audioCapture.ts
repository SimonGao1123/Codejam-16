// Turns the practice session's microphone stream into 16 kHz mono float32 chunks
// (the worklet does the downsampling from the mic's native rate).

import workletUrl from './audioCaptureWorklet.ts?worker&url'

export interface MicCapture {
  stop(): void
}

/** Calls `onChunk` with ~128 ms of 16 kHz float32 audio at a time until stop(). */
export async function captureMic(
  stream: MediaStream,
  onChunk: (samples: Float32Array) => void
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
  chunker.port.onmessage = (e: MessageEvent<Float32Array>) => onChunk(e.data)
  source.connect(chunker)
  return {
    stop() {
      source.disconnect()
      chunker.port.onmessage = null
      void context.close()
    }
  }
}
