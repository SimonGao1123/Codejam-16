export type PlatformId = 'mac' | 'windows' | 'linux'

export interface Platform {
  id: PlatformId
  name: string
  extension: string // how the platform's file is recognised among a release's files
  needs: string
  untested?: boolean // shown as "Not tested yet"; remove once someone has run it there
  firstOpen: string[] // the app isn't signed with a paid certificate, so each system asks first
}

export const platforms: Platform[] = [
  {
    id: 'mac',
    name: 'Mac',
    extension: '.dmg',
    needs: 'Apple Silicon, macOS 15 or later',
    firstOpen: [
      'Open the download and drag Colloquia to Applications, then open it there.',
      'macOS says it was “Not Opened”. Click Done, not Move to Trash.',
      'In System Settings → Privacy & Security, scroll to Security and click Open Anyway.',
      'Allow the camera and microphone.'
    ]
  },
  {
    id: 'windows',
    name: 'Windows',
    extension: '.exe',
    needs: '64-bit Windows',
    untested: true,
    firstOpen: [
      'Run the download.',
      'If SmartScreen appears, click More info, then Run anyway.',
      'Allow the camera and microphone if asked.'
    ]
  },
  {
    id: 'linux',
    name: 'Linux',
    extension: '.AppImage',
    needs: '64-bit, glibc 2.35 or later (Ubuntu 22.04+)',
    untested: true,
    firstOpen: [
      'Make the download executable: right-click → Properties, or run chmod +x on it.',
      'Open it.',
      'Allow the camera and microphone if asked.'
    ]
  }
]
