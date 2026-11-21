// Writes practice recordings to app/temp/recordings during development:
// <stamp>.webm (webcam + mic) and <stamp>.jsonl (one message per line, as sent to Python).

import { app, ipcMain, protocol } from 'electron'
import { randomUUID } from 'crypto'
import { createReadStream, createWriteStream, type WriteStream } from 'fs'
import { mkdir, rm, stat } from 'fs/promises'
import { join } from 'path'
import { Readable } from 'stream'

interface Recording {
  video: WriteStream
  data: WriteStream
  paths: { video: string; data: string; playbackUrl: string }
}

let current: Recording | null = null

const close = (s: WriteStream): Promise<void> =>
  new Promise((resolve, reject) => {
    if (s.errored) {
      reject(s.errored)
      return
    }
    s.once('error', reject)
    s.end(resolve)
  })

async function serveRecording(path: string, request: Request): Promise<Response> {
  const file = await stat(path).catch(() => null)
  if (!file?.isFile() || file.size === 0) return new Response(null, { status: 404 })

  const range = request.headers.get('range')
  const bounds = range?.match(/^bytes=(\d+)-(\d*)$/)
  const start = bounds ? Number(bounds[1]) : 0
  const end = bounds?.[2] ? Math.min(Number(bounds[2]), file.size - 1) : file.size - 1
  if ((range && !bounds) || start > end) {
    return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${file.size}` } })
  }

  const headers = new Headers({
    'Content-Type': 'video/webm',
    'Accept-Ranges': 'bytes',
    'Content-Length': String(end - start + 1)
  })
  if (bounds) headers.set('Content-Range', `bytes ${start}-${end}/${file.size}`)
  const body = request.method === 'HEAD'
    ? null
    : (Readable.toWeb(createReadStream(path, { start, end })) as ReadableStream<Uint8Array>)
  return new Response(body, { headers, status: bounds ? 206 : 200 })
}

export function registerWebcamRecorderIpc(): void {
  const dir = app.isPackaged
    ? join(app.getPath('userData'), 'temp', 'recordings')
    : join(app.getAppPath(), 'temp', 'recordings')

  protocol.handle('practice-recording', (request) => {
    const url = new URL(request.url)
    if (url.hostname !== 'media' || !/^\/[a-f0-9-]{36}\.webm$/.test(url.pathname)) {
      return new Response('Recording not found', { status: 404 })
    }
    return serveRecording(join(dir, url.pathname.slice(1)), request)
  })

  ipcMain.handle('webcam-recorder:start', async () => {
    if (current) await Promise.all([close(current.video), close(current.data)])
    await mkdir(dir, { recursive: true })
    const base = randomUUID()
    const paths = {
      video: join(dir, `${base}.webm`),
      data: join(dir, `${base}.jsonl`),
      playbackUrl: `practice-recording://media/${base}.webm`
    }
    const video = createWriteStream(paths.video)
    const data = createWriteStream(paths.data)
    for (const stream of [video, data]) {
      stream.on('error', (error) => console.error('Recording write failed', error))
    }
    current = { video, data, paths }
    return { dir, base }
  })

  ipcMain.on('webcam-recorder:chunk', (_, chunk: Uint8Array) => {
    current?.video.write(Buffer.from(chunk))
  })

  ipcMain.on('webcam-recorder:line', (_, line: string) => {
    current?.data.write(line + '\n')
  })

  // discard: the session ended before calibration finished, so the files are deleted
  ipcMain.handle('webcam-recorder:stop', async (_, discard?: boolean) => {
    if (!current) return null
    const { video, data, paths } = current
    current = null
    await Promise.all([close(video), close(data)])
    if (!discard) return paths
    await Promise.all([rm(paths.video, { force: true }), rm(paths.data, { force: true })])
    return null
  })
}
