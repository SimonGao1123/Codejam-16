import { contextBridge, ipcRenderer } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'

// the Python analysis service's port, passed by the main process (see src/main/analysisService.ts)
const portArg = process.argv.find((a) => a.startsWith('--analysis-service-port='))

// APIs exposed to the renderer as window.api
const api = {
  servicePort: Number(portArg?.split('=')[1]) || 8766,
  webcamRecorder: {
    start: (): Promise<{ dir: string; base: string }> =>
      ipcRenderer.invoke('webcam-recorder:start'),
    chunk: (chunk: Uint8Array): void => ipcRenderer.send('webcam-recorder:chunk', chunk),
    line: (line: string): void => ipcRenderer.send('webcam-recorder:line', line),
    stop: (discard = false): Promise<{ video: string; data: string; playbackUrl: string } | null> =>
      ipcRenderer.invoke('webcam-recorder:stop', discard)
  }
}

export type Api = typeof api

if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('api', api)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (define in dts)
  window.electron = electronAPI
  // @ts-ignore (define in dts)
  window.api = api
}
