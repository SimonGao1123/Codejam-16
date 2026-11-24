// The script text box with its Attach PDF / Clear buttons (a PDF's text is read into the box for
// review). Shared by the practice and upload pages.

import { useRef, useState } from 'react'

import { pdfText } from '../script/pdfText'

type Props = {
  script: string
  setScript: (text: string) => void
  disabled: boolean
  placeholder: string
}

export default function ScriptEditor({
  script,
  setScript,
  disabled,
  placeholder
}: Props): React.JSX.Element {
  const [pdfStatus, setPdfStatus] = useState<{ text: string; error: boolean } | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function attachPdf(file: File | undefined): Promise<void> {
    if (!file) return
    setPdfStatus({ text: `Reading ${file.name}…`, error: false })
    try {
      const text = await pdfText(file)
      setScript(text)
      setPdfStatus({ text: `Read from ${file.name}: check the text above`, error: false })
    } catch (err) {
      setPdfStatus({ text: err instanceof Error ? err.message : String(err), error: true })
    } finally {
      if (fileRef.current) fileRef.current.value = '' // allow attaching the same file again
    }
  }

  return (
    <div className="ph-script-edit">
      <textarea
        value={script}
        onChange={(e) => setScript(e.target.value)}
        disabled={disabled}
        placeholder={placeholder}
        aria-label="Your script"
      />
      <div className="ph-script-bar">
        <button
          type="button"
          className="ph-script-button"
          onClick={() => fileRef.current?.click()}
          disabled={disabled}
        >
          Attach PDF
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/pdf,.pdf"
          hidden
          onChange={(e) => void attachPdf(e.target.files?.[0])}
        />
        {script && (
          <button
            type="button"
            className="ph-script-button"
            onClick={() => {
              setScript('')
              setPdfStatus(null)
            }}
            disabled={disabled}
          >
            Clear
          </button>
        )}
        {pdfStatus && (
          <span className={pdfStatus.error ? 'ph-error' : 'ph-webcam-note'}>{pdfStatus.text}</span>
        )}
      </div>
    </div>
  )
}
