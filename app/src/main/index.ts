import { app, shell, BrowserWindow, net, protocol, systemPreferences } from 'electron'
import { join, normalize } from 'path'
import { pathToFileURL } from 'url'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { registerWebcamRecorderIpc } from './webcamRecorder'

// The built UI is served from app://renderer/ instead of file://, because MediaPipe loads its
// models and WebAssembly with fetch(), which Chromium doesn't allow for file:// URLs.
protocol.registerSchemesAsPrivileged([
  { scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true } }
])

function serveRenderer(): void {
  const root = join(__dirname, '../renderer')
  protocol.handle('app', (request) => {
    const path = normalize(join(root, decodeURIComponent(new URL(request.url).pathname)))
    if (!path.startsWith(root)) return new Response('not found', { status: 404 })
    return net.fetch(pathToFileURL(path).toString())
  })
}

function createWindow(): void {
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
      backgroundThrottling: false
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
  serveRenderer()

  // macOS shows its camera / microphone permission prompts the first time
  if (process.platform === 'darwin') {
    await systemPreferences.askForMediaAccess('camera')
    await systemPreferences.askForMediaAccess('microphone')
  }

  createWindow()

  app.on('activate', function () {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
