import { app, ipcMain } from 'electron'
import { randomUUID } from 'crypto'
import { readFile, rename, writeFile } from 'fs/promises'
import { join } from 'path'
import { deleteSavedRecording } from './webcamRecorder'

export interface PracticeFolder {
  id: string
  name: string
}

export interface PracticeAttempt {
  id: string
  folderId: string | null
  completedAt: number
  score: { points: number; maximum: number; measured: number } | null
  name?: string // set by the user; shown instead of the date
}

export interface PracticeLibrary {
  folders: PracticeFolder[]
  attempts: PracticeAttempt[]
}

export function registerPracticeLibraryIpc(): void {
  const path = join(app.getPath('userData'), 'practice-library.json')
  const tempPath = `${path}.${randomUUID()}.tmp`
  let pendingWrite = Promise.resolve()

  async function read(): Promise<PracticeLibrary> {
    try {
      return JSON.parse(await readFile(path, 'utf8')) as PracticeLibrary
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return { folders: [], attempts: [] }
      throw error
    }
  }

  function update(
    change: (library: PracticeLibrary) => void | Promise<void>
  ): Promise<PracticeLibrary> {
    const result = pendingWrite.then(async () => {
      const library = await read()
      await change(library)
      await writeFile(tempPath, JSON.stringify(library), 'utf8')
      await rename(tempPath, path)
      return library
    })
    pendingWrite = result.then(
      () => undefined,
      () => undefined
    )
    return result
  }

  /** A folder name that's not empty, at most 80 characters, and not taken (case-insensitive;
   *  "Unfiled" is reserved). `except`: the folder being renamed may keep its own name. */
  function validFolderName(library: PracticeLibrary, name: string, except?: string): string {
    const trimmed = typeof name === 'string' ? name.trim() : ''
    if (!trimmed || trimmed.length > 80) throw new Error('Invalid folder name.')
    const normalized = trimmed.toLowerCase()
    if (
      normalized === 'unfiled' ||
      library.folders.some((f) => f.id !== except && f.name.toLowerCase() === normalized)
    ) {
      throw new Error('A folder with that name already exists.')
    }
    return trimmed
  }

  function checkFolder(library: PracticeLibrary, folderId: string | null): void {
    if (folderId !== null && !library.folders.some((folder) => folder.id === folderId)) {
      throw new Error('Practice folder does not exist.')
    }
  }

  ipcMain.handle('practice-library:list', () => pendingWrite.then(read))
  ipcMain.handle('practice-library:delete-attempt', (_, id: string) => {
    if (typeof id !== 'string') throw new Error('Invalid practice attempt ID.')
    return update(async (library) => {
      if (id.endsWith('.webm')) await deleteSavedRecording(id)
      library.attempts = library.attempts.filter((attempt) => attempt.id !== id)
    })
  })
  // deletes only the video (and its event log): the attempt keeps its score, name and folder
  ipcMain.handle('practice-library:delete-recording', (_, id: string) =>
    update(() => deleteSavedRecording(id))
  )
  ipcMain.handle('practice-library:create-folder', (_, name: string) =>
    update((library) => {
      library.folders.push({ id: randomUUID(), name: validFolderName(library, name) })
    })
  )
  ipcMain.handle('practice-library:rename-folder', (_, id: string, name: string) =>
    update((library) => {
      const folder = library.folders.find((f) => f.id === id)
      if (!folder) throw new Error('Practice folder does not exist.')
      folder.name = validFolderName(library, name, id)
    })
  )
  // deleteAttempts: also delete the folder's attempts (recordings and scores); otherwise they move
  // to Unfiled
  ipcMain.handle('practice-library:delete-folder', (_, id: string, deleteAttempts: boolean) =>
    update(async (library) => {
      checkFolder(library, id)
      const inFolder = library.attempts.filter((attempt) => attempt.folderId === id)
      if (deleteAttempts) {
        for (const attempt of inFolder)
          if (attempt.id.endsWith('.webm')) await deleteSavedRecording(attempt.id)
        library.attempts = library.attempts.filter((attempt) => attempt.folderId !== id)
      } else {
        for (const attempt of inFolder) attempt.folderId = null
      }
      library.folders = library.folders.filter((folder) => folder.id !== id)
    })
  )
  // name: empty clears it (the attempt is shown by its date again)
  ipcMain.handle(
    'practice-library:rename-attempt',
    (_, entry: Pick<PracticeAttempt, 'id' | 'completedAt'>, name: string) =>
      update((library) => {
        const trimmed = typeof name === 'string' ? name.trim().slice(0, 80) : ''
        let attempt = library.attempts.find(({ id }) => id === entry.id)
        if (!attempt) {
          // a recording that was never scored has no entry yet
          attempt = { ...entry, folderId: null, score: null }
          library.attempts.push(attempt)
        }
        if (trimmed) attempt.name = trimmed
        else delete attempt.name
      })
  )
  ipcMain.handle('practice-library:save-attempt', (_, attempt: PracticeAttempt) =>
    update((library) => {
      checkFolder(library, attempt.folderId)
      const existing = library.attempts.find((entry) => entry.id === attempt.id)
      if (existing) {
        existing.completedAt = attempt.completedAt
        existing.score = attempt.score
      } else {
        library.attempts.push(attempt)
      }
    })
  )
  ipcMain.handle(
    'practice-library:move-attempt',
    (_, entry: Pick<PracticeAttempt, 'id' | 'completedAt'>, folderId: string | null) =>
      update((library) => {
        checkFolder(library, folderId)
        const attempt = library.attempts.find(({ id }) => id === entry.id)
        if (attempt) attempt.folderId = folderId
        else library.attempts.push({ ...entry, folderId, score: null })
      })
  )
}
