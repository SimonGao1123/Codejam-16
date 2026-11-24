// The newest GitHub release and its files. Read once when the site is built and again in the
// visitor's browser, so a new release shows up without redeploying. If GitHub can't be reached the
// last known links stay; older releases remain downloadable.

import { platforms, type PlatformId } from './platforms'

export const REPO_URL = 'https://github.com/SimonGao1123/Colloquia-Codejam-16'
const API_URL = 'https://api.github.com/repos/SimonGao1123/Colloquia-Codejam-16/releases/latest'
const FILES_URL = `${REPO_URL}/releases/download/`

export interface ReleaseFile {
  url: string
  size: number // bytes
  version: string
}
export type Release = Record<PlatformId, ReleaseFile>

export const FALLBACK: Release = {
  mac: {
    url: `${FILES_URL}v1.0.0/Colloquia-1.0.0-arm64.dmg`,
    size: 285276630,
    version: '1.0.0'
  },
  windows: {
    url: `${FILES_URL}v1.0.0/Colloquia-1.0.0-setup.exe`,
    size: 263409194,
    version: '1.0.0'
  },
  linux: {
    url: `${FILES_URL}v1.0.0/Colloquia-1.0.0.AppImage`,
    size: 306892825,
    version: '1.0.0'
  }
}

/** Picks each platform's file out of GitHub's reply; a platform without one keeps `previous`. */
export function parseRelease(reply: unknown, previous: Release): Release {
  const { tag_name: tag, assets } = (reply ?? {}) as { tag_name?: unknown; assets?: unknown }
  if (typeof tag !== 'string' || !Array.isArray(assets)) return previous
  const release = { ...previous }
  for (const { id, extension } of platforms) {
    const file = assets.find(
      (asset) =>
        typeof asset?.name === 'string' &&
        asset.name.endsWith(extension) &&
        typeof asset.size === 'number' &&
        typeof asset.browser_download_url === 'string' &&
        asset.browser_download_url.startsWith(FILES_URL)
    )
    if (file)
      release[id] = {
        url: file.browser_download_url,
        size: file.size,
        version: tag.replace(/^v/, '')
      }
  }
  return release
}

export async function fetchRelease(previous: Release): Promise<Release> {
  try {
    const response = await fetch(API_URL, {
      headers: { Accept: 'application/vnd.github+json' },
      signal: AbortSignal.timeout(5000)
    })
    return response.ok ? parseRelease(await response.json(), previous) : previous
  } catch {
    return previous
  }
}

export const megabytes = (bytes: number): string => `${Math.round(bytes / 1024 / 1024)} MB`
