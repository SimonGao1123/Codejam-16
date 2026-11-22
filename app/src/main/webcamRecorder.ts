// Writes practice recordings to app/temp/recordings during development:
// <stamp>.webm (webcam + mic) and <stamp>.jsonl (one message per line, as sent to Python).

import { app, ipcMain, protocol } from 'electron'
import { randomUUID } from 'crypto'
import { createReadStream, createWriteStream, type WriteStream } from 'fs'
import { mkdir, readdir, stat, unlink } from 'fs/promises'
import { join } from 'path'
import { Readable } from 'stream'

interface Recording {
  video: WriteStream
  data: WriteStream
  paths: { id: string; video: string; data: string; playbackUrl: string }
}

let current: Recording | null = null
const uploadedMedia = new Map<string, { path: string; contentType: string }>()

function contentTypeFor(path: string): string {
  const extension = path.slice(path.lastIndexOf('.') + 1).toLowerCase()
  return (
    {
      mp4: 'video/mp4',
      webm: 'video/webm',
      mov: 'video/quicktime',
      mkv: 'video/x-matroska',
      wav: 'audio/wav',
      mp3: 'audio/mpeg',
      m4a: 'audio/mp4'
    }[extension] ?? 'application/octet-stream'
  )
const recordingName = /^[a-f0-9-]{36}\.webm$/
const playbackUrl = (name: string): string => `practice-recording://media/${name}`
const closingVideos = new Set<string>()

function recordingsDirectory(): string {
  return app.isPackaged
    ? join(app.getPath('userData'), 'temp', 'recordings')
    : join(app.getAppPath(), 'temp', 'recordings')
}

export interface SavedRecording {
  id: string
  savedAt: number
  playbackUrl: string
}

function isMissing(error: unknown): boolean {
  return (error as NodeJS.ErrnoException | null)?.code === 'ENOENT'
}

export async function deleteSavedRecording(id: string): Promise<void> {
  if (!recordingName.test(id)) throw new Error('Invalid recording ID.')
  const dir = recordingsDirectory()
  const video = join(dir, id)
  if (video === current?.paths.video || closingVideos.has(video)) {
    throw new Error('The recording is still being saved.')
  }

  // Keep the video available for another delete attempt if removing the event log fails.
  for (const path of [join(dir, id.replace(/\.webm$/, '.jsonl')), video]) {
    try {
      await unlink(path)
    } catch (error) {
      if (!isMissing(error)) throw error
    }
  }
}

const close = (s: WriteStream): Promise<void> =>
  new Promise((resolve, reject) => {
    if (s.errored) {
      reject(s.errored)
      return
    }
    s.once('error', reject)
    s.end(resolve)
  })

async function serveMedia(path: string, request: Request, contentType: string): Promise<Response> {
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
    'Content-Type': contentType,
    'Accept-Ranges': 'bytes',
    'Content-Length': String(end - start + 1)
  })
  if (bounds) headers.set('Content-Range', `bytes ${start}-${end}/${file.size}`)
  const body =
    request.method === 'HEAD'
      ? null
      : (Readable.toWeb(createReadStream(path, { start, end })) as ReadableStream<Uint8Array>)
  return new Response(body, { headers, status: bounds ? 206 : 200 })
}

/**
 * Gives a user-selected file a capability URL. The renderer never supplies a filesystem path to
 * the protocol handler, and the URL supports the byte ranges Chromium needs for seeking/replay.
 */
export function registerUploadedMediaIpc(): void {
  protocol.handle('uploaded-media', (request) => {
    const id = new URL(request.url).hostname
    const media = uploadedMedia.get(id)
    if (!media) return new Response('Recording not found', { status: 404 })
    return serveMedia(media.path, request, media.contentType)
  })

  ipcMain.handle('uploaded-media:register', async (_, path: string) => {
    const file = await stat(path).catch(() => null)
    if (!file?.isFile() || file.size === 0)
      throw new Error('The selected recording is unavailable.')
    const id = randomUUID()
    uploadedMedia.set(id, { path, contentType: contentTypeFor(path) })
    return `uploaded-media://${id}/recording`
  })

  ipcMain.handle('uploaded-media:release', (_, url: string) => {
    try {
      const parsed = new URL(url)
      if (parsed.protocol === 'uploaded-media:') uploadedMedia.delete(parsed.hostname)
    } catch {
      // Releasing a stale URL is harmless.
    }
  })
}

export function registerWebcamRecorderIpc(): void {
  const dir = recordingsDirectory()

  protocol.handle('practice-recording', (request) => {
    const url = new URL(request.url)
    if (url.hostname !== 'media' || !recordingName.test(url.pathname.slice(1))) {
      return new Response('Recording not found', { status: 404 })
    }
    return serveMedia(join(dir, url.pathname.slice(1)), request, 'video/webm')
  })

  ipcMain.handle('webcam-recorder:list', async (): Promise<SavedRecording[]> => {
    const entries = await readdir(dir, { withFileTypes: true }).catch((error: unknown) => {
      if (isMissing(error)) return []
      throw error
    })
    const recordings = await Promise.all(
      entries
        .filter((entry) => entry.isFile() && recordingName.test(entry.name))
        .map(async ({ name }) => {
          const path = join(dir, name)
          const file = await stat(path).catch((error: unknown) => {
            if (isMissing(error)) return null
            throw error
          })
          if (!file?.isFile() || !file.size || path === current?.paths.video || closingVideos.has(path)) {
            return null
          }
          return { id: name, savedAt: file.mtimeMs, playbackUrl: playbackUrl(name) }
        })
    )
    return recordings.filter((recording) => recording !== null).sort((a, b) => b.savedAt - a.savedAt)
  })

  ipcMain.handle('webcam-recorder:start', async () => {
    if (current) await Promise.all([close(current.video), close(current.data)])
    await mkdir(dir, { recursive: true })
    const base = randomUUID()
    const id = `${base}.webm`
    const paths = {
      id,
      video: join(dir, id),
      data: join(dir, `${base}.jsonl`),
      playbackUrl: playbackUrl(id)
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

  ipcMain.handle('webcam-recorder:stop', async () => {
    if (!current) return null
    const { video, data, paths } = current
    closingVideos.add(paths.video)
    current = null
    try {
      await Promise.all([close(video), close(data)])
    } finally {
      closingVideos.delete(paths.video)
    }
    return paths
  })
}
