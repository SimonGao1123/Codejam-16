// Reads the text out of a script PDF with pdf.js. Text PDFs only (exported from Word, Google Docs,
// slides...): a scanned or photographed page has no text to read, and we don't run OCR.

import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

GlobalWorkerOptions.workerSrc = workerUrl

export class NoTextError extends Error {
  constructor() {
    super(
      "This PDF has no selectable text (it's probably a scan or a photo). Paste the script instead."
    )
  }
}

/** The PDF's text, one line per text line and a blank line between pages. */
export async function pdfText(file: File): Promise<string> {
  const task = getDocument({ data: new Uint8Array(await file.arrayBuffer()) })
  const pdf = await task.promise
  try {
    const pages: string[] = []
    for (let n = 1; n <= pdf.numPages; n++) {
      const page = await pdf.getPage(n)
      const content = await page.getTextContent()
      let text = ''
      for (const item of content.items) {
        if (!('str' in item)) continue // marked-content markers, not text
        text += item.str + (item.hasEOL ? '\n' : '')
      }
      pages.push(
        text
          .replace(/[ \t]+/g, ' ')
          .replace(/ *\n */g, '\n')
          .trim()
      )
    }
    const all = pages.filter(Boolean).join('\n\n')
    if (all.replace(/\s/g, '').length < 20) throw new NoTextError()
    return all
  } finally {
    void task.destroy()
  }
}
