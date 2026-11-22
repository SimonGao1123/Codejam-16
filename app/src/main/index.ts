import { app, shell, BrowserWindow, net, protocol, systemPreferences } from 'electron'
import { join, normalize } from 'path'
import { pathToFileURL } from 'url'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { startAnalysisService } from './analysisService'
import { registerUploadedMediaIpc, registerWebcamRecorderIpc } from './webcamRecorder'

// The built UI is served from app://renderer/ instead of file://, because MediaPipe loads its
// models and WebAssembly with fetch(), which Chromium doesn't allow for file:// URLs.
protocol.registerSchemesAsPrivileged([
  { scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true } },
  { scheme: 'practice-recording', privileges: { standard: true, secure: true, stream: true } },
  // Uploaded files use the same range-request playback path as practice recordings. Blob URLs are
  // renderer-owned and can become unreliable when the analysis preview is replaced by the player.
  { scheme: 'uploaded-media', privileges: { standard: true, secure: true, stream: true } }
])

function serveRenderer(): void {
  const root = join(__dirname, '../renderer')
  protocol.handle('app', (request) => {
    const path = normalize(join(root, decodeURIComponent(new URL(request.url).pathname)))
    if (!path.startsWith(root)) return new Response('not found', { status: 404 })
    return net.fetch(pathToFileURL(path).toString())
  })
}

function createWindow(servicePort: number): void {
  const mainWindow = new BrowserWindow({
    width: 1320,
    height: 900,
    show: false,
    autoHideMenuBar: true,
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      // keep analysing when the window is covered (e.g. slides on top) or minimized
      backgroundThrottling: false,
      // the analysis service's port, read by the preload script
      additionalArguments: [`--analysis-service-port=${servicePort}`]
    }
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  // Dev: load the Vite dev server (hot reload). Production: load the built UI over app://.
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadURL('app://renderer/index.html')
  }
}

app.whenReady().then(async () => {
  electronApp.setAppUserModelId('com.electron')

  // F12 opens DevTools in dev; Cmd/Ctrl+R reload is disabled in production
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  registerWebcamRecorderIpc()
  registerUploadedMediaIpc()
  serveRenderer()
  // the Python analysis service starts loading its models while the window opens
  const servicePort = await startAnalysisService()

  // macOS shows its camera / microphone permission prompts the first time
  if (process.platform === 'darwin') {
    await systemPreferences.askForMediaAccess('camera')
    await systemPreferences.askForMediaAccess('microphone')
  }

  createWindow(servicePort)
})

// Closing the window quits the app on every platform, including macOS (where apps normally stay
// open in the Dock); quitting also stops the analysis service (see analysisService.ts).
app.on('window-all-closed', () => app.quit())
