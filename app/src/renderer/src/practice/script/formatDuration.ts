/** 330 -> "5 min 30 sec" */
export function formatDuration(total: number): string {
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return [minutes && `${minutes} min`, seconds && `${seconds} sec`].filter(Boolean).join(' ')
}
