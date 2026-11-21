import { contextBridge, ipcRenderer } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'

// APIs exposed to the renderer as window.api
const api = {
  webcamRecorder: {
    start: (): Promise<{ dir: string; base: string }> =>
      ipcRenderer.invoke('webcam-recorder:start'),
    chunk: (chunk: Uint8Array): void => ipcRenderer.send('webcam-recorder:chunk', chunk),
    line: (line: string): void => ipcRenderer.send('webcam-recorder:line', line),
    stop: (): Promise<{ video: string; data: string } | null> =>
      ipcRenderer.invoke('webcam-recorder:stop')
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
