// Starts the Python analysis service (../service) alongside the app and stops it on quit.
// It gets a free port, which the page learns through the preload script (window.api.servicePort).
//
// To run the service yourself instead (e.g. with --debug), start it first and launch the app with
// ANALYSIS_SERVICE_PORT=8766; the app then connects to that port and doesn't start its own.

import { app } from 'electron'
import { spawn, type ChildProcess } from 'child_process'
import { existsSync } from 'fs'
import { createServer } from 'net'
import { homedir } from 'os'
import { join, resolve } from 'path'

const RESTART_DELAY_MS = 2000
const MAX_RESTARTS = 5

let child: ChildProcess | null = null
let quitting = false
let restarts = 0

/** A port nothing is listening on right now. */
function freePort(): Promise<number> {
  return new Promise((done, fail) => {
    const server = createServer()
    server.unref()
    server.on('error', fail)
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as { port: number }
      server.close(() => done(port))
    })
  })
}

/** uv isn't on PATH when the app is opened from the Finder / Dock, so look in the usual places. */
function findUv(): string {
  const candidates = [
    join(homedir(), '.local', 'bin', 'uv'),
    join(homedir(), '.cargo', 'bin', 'uv'),
    '/opt/homebrew/bin/uv',
    '/usr/local/bin/uv'
  ]
  return candidates.find((p) => existsSync(p)) ?? 'uv'
}

function start(port: number): void {
  const serviceDir = resolve(__dirname, '../../../service') // app/out/main -> repo/service
  if (!existsSync(serviceDir)) {
    console.error(`[service] not found at ${serviceDir}; analysis won't be available`)
    return
  }
  const proc = spawn(
    findUv(),
    ['run', 'python', '-m', 'presentation_service', '--port', String(port), '--exit-with-parent'],
    {
      cwd: serviceDir,
      // stdin stays open as a lifeline: if Electron dies without cleaning up, the service sees
      // stdin close and exits instead of being left running
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: process.platform !== 'win32', // own process group, so stop() also ends uv's child
      env: { ...process.env, PYTHONUNBUFFERED: '1' }
    }
  )
  child = proc
  const log = (data: Buffer): void => {
    for (const line of data.toString().trimEnd().split('\n')) console.log(`[service] ${line}`)
  }
  proc.stdout?.on('data', log)
  proc.stderr?.on('data', log)
  proc.on('error', (err) =>
    console.error(`[service] couldn't start (is uv installed?): ${err.message}`)
  )
  proc.on('exit', (code, signal) => {
    if (child === proc) child = null
    if (quitting) return
    console.error(`[service] exited (${signal ?? `code ${code}`})`)
    if (restarts++ < MAX_RESTARTS) setTimeout(() => !quitting && start(port), RESTART_DELAY_MS)
  })
}

function stop(): void {
  quitting = true
  const proc = child
  if (!proc?.pid) return
  try {
    if (process.platform === 'win32') spawn('taskkill', ['/pid', String(proc.pid), '/T', '/F'])
    else process.kill(-proc.pid, 'SIGTERM') // the whole group: uv and the Python it started
  } catch {
    // already gone
  }
}

/** Starts the service (unless ANALYSIS_SERVICE_PORT points at one already running); returns its port. */
export async function startAnalysisService(): Promise<number> {
  const external = Number(process.env.ANALYSIS_SERVICE_PORT)
  if (external) {
    console.log(`[service] using the service already running on port ${external}`)
    return external
  }
  const port = await freePort()
  start(port)
  app.on('will-quit', stop)
  return port
}
