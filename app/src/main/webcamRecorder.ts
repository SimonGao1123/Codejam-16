// Writes webcam recordings from the renderer to ~/Movies/Presentation Helper (Videos on Windows):
// <stamp>.webm (webcam + mic) and <stamp>.jsonl (one message per line, as sent to Python).

import { app, ipcMain } from 'electron'
import { createWriteStream, mkdirSync, type WriteStream } from 'fs'
import { join } from 'path'

interface Recording {
  video: WriteStream
  data: WriteStream
  paths: { video: string; data: string }
}

let current: Recording | null = null

const close = (s: WriteStream): Promise<void> => new Promise((resolve) => s.end(resolve))

export function registerWebcamRecorderIpc(): void {
  ipcMain.handle('webcam-recorder:start', async () => {
    if (current) await Promise.all([close(current.video), close(current.data)])
    const dir = join(app.getPath('videos'), 'Presentation Helper')
    mkdirSync(dir, { recursive: true })
    const base = new Date().toISOString().replace(/[:.]/g, '-').replace('T', '_').slice(0, 19)
    const paths = { video: join(dir, `${base}.webm`), data: join(dir, `${base}.jsonl`) }
    current = { video: createWriteStream(paths.video), data: createWriteStream(paths.data), paths }
    return { dir, base }
  })

  ipcMain.on('webcam-recorder:chunk', (_, chunk: Uint8Array) => {
    current?.video.write(Buffer.from(chunk))
  })

  ipcMain.on('webcam-recorder:line', (_, line: string) => {
    current?.data.write(line + '\n')
  })

  ipcMain.handle('webcam-recorder:stop', async () => {
    if (!current) return null
    const { video, data, paths } = current
    current = null
    await Promise.all([close(video), close(data)])
    return paths
  })
}
