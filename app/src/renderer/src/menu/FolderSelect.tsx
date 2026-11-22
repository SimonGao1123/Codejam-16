import type { PracticeFolder } from '../../../main/practiceLibrary'

export default function FolderSelect({
  folders,
  value,
  onChange,
  label,
  disabled
}: {
  folders: PracticeFolder[]
  value: string | null
  onChange: (folderId: string | null) => void
  label: string
  disabled?: boolean
}): React.JSX.Element {
  return (
    <label className="ph-folder-select">
      {label}
      <select
        value={value ?? ''}
        onChange={(event) => onChange(event.target.value || null)}
        disabled={disabled}
      >
        <option value="">Unfiled</option>
        {folders.map(({ id, name }) => (
          <option key={id} value={id}>
            {name}
          </option>
        ))}
      </select>
    </label>
  )
}
