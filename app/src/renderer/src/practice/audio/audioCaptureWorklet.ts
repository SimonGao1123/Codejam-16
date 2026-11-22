// Audio worklet: downsamples the microphone to 16 kHz mono and posts ~128 ms chunks to the main
// thread for sending to Python. Loaded by audioCapture.ts; runs on the audio rendering thread.
//
// Resampling is done here instead of with AudioContext({ sampleRate: 16000 }), because Chromium's
// built-in resampling outputs silence for some input sources.

// The audio worklet globals aren't in TypeScript's DOM types.
declare class AudioWorkletProcessor {
  readonly port: MessagePort
}
declare function registerProcessor(name: string, processor: typeof AudioWorkletProcessor): void
declare const sampleRate: number // the context's (= the mic's) sample rate
declare const currentTime: number

const TARGET_RATE = 16000
const CHUNK = 2048 // output samples per message (128 ms at 16 kHz)

class MicChunker extends AudioWorkletProcessor {
  private buffer = new Float32Array(CHUNK)
  private filled = 0
  // averaging decimator: each output sample is the mean of the input samples it covers,
  // which also filters out frequencies above 8 kHz well enough for speech
  private step = sampleRate / TARGET_RATE // input samples per output sample, e.g. 3 for 48 kHz
  private pos = 0 // input samples since the last output sample
  private sum = 0
  private count = 0

  process(inputs: Float32Array[][]): boolean {
    const mono = inputs[0]?.[0] // first channel of the first input (mixed down to mono)
    if (!mono) return true
    for (let i = 0; i < mono.length; i++) {
      this.sum += mono[i]
      this.count++
      this.pos++
      if (this.pos >= this.step) {
        this.push(this.sum / this.count, currentTime + (i + 1) / sampleRate)
        this.pos -= this.step
        this.sum = 0
        this.count = 0
      }
    }
    return true
  }

  private push(sample: number, endTime: number): void {
    this.buffer[this.filled++] = sample
    if (this.filled === CHUNK) {
      this.port.postMessage({ samples: this.buffer, startTime: endTime - CHUNK / TARGET_RATE }, [
        this.buffer.buffer
      ])
      this.buffer = new Float32Array(CHUNK)
      this.filled = 0
    }
  }
}

registerProcessor('mic-chunker', MicChunker)
