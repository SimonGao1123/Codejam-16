// What the webcam overlay can draw, for the Settings tab's advanced webcam settings. The skeleton
// parts are drawn by webcamDraw.ts. The stats are either computed there or come from the service
// as status lines tagged with these ids (`stat`, see docs/webcam-protocol.md).

export const skeletonParts = [
  { id: 'brows', label: 'Eyebrows' },
  { id: 'eyes', label: 'Eyes' },
  { id: 'head', label: 'Nose, ears and mouth' },
  { id: 'shoulders', label: 'Shoulders' },
  { id: 'arms', label: 'Arms' },
  { id: 'wrists', label: 'Wrists' },
  { id: 'hands', label: 'Hands and fingers' },
  { id: 'torso', label: 'Torso' },
  { id: 'rest', label: 'Hand rest spots' }
] as const

export const statParts = [
  { id: 'fps', label: 'Frame rate' },
  { id: 'hands_seen', label: 'Hands seen' },
  { id: 'wpm', label: 'Live WPM' },
  { id: 'pace', label: 'Average and desired WPM' },
  { id: 'tilt', label: 'Shoulder tilt' },
  { id: 'hands', label: 'Hand movement' },
  { id: 'hand_angles', label: 'Hand angles' },
  { id: 'eye_level', label: 'Eye level' },
  { id: 'voice', label: 'Pitch and volume' },
  { id: 'calibration', label: 'Calibration' }
] as const

export type SkeletonPart = (typeof skeletonParts)[number]['id']
export type StatPart = (typeof statParts)[number]['id']

/** Every part on, except those saved as off. */
export function readParts<Id extends string>(
  parts: readonly { id: Id }[],
  saved?: Partial<Record<Id, boolean>>
): Record<Id, boolean> {
  return Object.fromEntries(parts.map(({ id }) => [id, saved?.[id] !== false])) as Record<
    Id,
    boolean
  >
}
