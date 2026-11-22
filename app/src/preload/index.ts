import { contextBridge, ipcRenderer, webUtils } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'
import type { PracticeAttempt, PracticeLibrary } from '../main/practiceLibrary'
import type { SavedRecording } from '../main/webcamRecorder'

// the Python analysis service's port, passed by the main process (see src/main/analysisService.ts)
const portArg = process.argv.find((a) => a.startsWith('--analysis-service-port='))

// APIs exposed to the renderer as window.api
const api = {
  servicePort: Number(portArg?.split('=')[1]) || 8766,
  practiceLibrary: {
    list: (): Promise<PracticeLibrary> => ipcRenderer.invoke('practice-library:list'),
    deleteAttempt: (id: string): Promise<PracticeLibrary> =>
      ipcRenderer.invoke('practice-library:delete-attempt', id),
    createFolder: (name: string): Promise<PracticeLibrary> =>
      ipcRenderer.invoke('practice-library:create-folder', name),
    renameFolder: (id: string, name: string): Promise<PracticeLibrary> =>
      ipcRenderer.invoke('practice-library:rename-folder', id, name),
    deleteFolder: (id: string, deleteAttempts: boolean): Promise<PracticeLibrary> =>
      ipcRenderer.invoke('practice-library:delete-folder', id, deleteAttempts),
    renameAttempt: (
      entry: Pick<PracticeAttempt, 'id' | 'completedAt'>,
      name: string
    ): Promise<PracticeLibrary> =>
      ipcRenderer.invoke('practice-library:rename-attempt', entry, name),
    saveAttempt: (attempt: PracticeAttempt): Promise<PracticeLibrary> =>
      ipcRenderer.invoke('practice-library:save-attempt', attempt),
    moveAttempt: (
      entry: Pick<PracticeAttempt, 'id' | 'completedAt'>,
      folderId: string | null
    ): Promise<PracticeLibrary> =>
      ipcRenderer.invoke('practice-library:move-attempt', entry, folderId)
  },
  webcamRecorder: {
    list: (): Promise<SavedRecording[]> => ipcRenderer.invoke('webcam-recorder:list'),
    start: (): Promise<{ dir: string; base: string }> =>
      ipcRenderer.invoke('webcam-recorder:start'),
    chunk: (chunk: Uint8Array): void => ipcRenderer.send('webcam-recorder:chunk', chunk),
    line: (line: string): void => ipcRenderer.send('webcam-recorder:line', line),
    stop: (): Promise<{ id: string; video: string; data: string; playbackUrl: string } | null> =>
      ipcRenderer.invoke('webcam-recorder:stop')
  },
  uploadMedia: {
    register: (file: File): Promise<string> =>
      ipcRenderer.invoke('uploaded-media:register', webUtils.getPathForFile(file)),
    release: (url: string): Promise<void> => ipcRenderer.invoke('uploaded-media:release', url)
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
