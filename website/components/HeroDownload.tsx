'use client'

import { useState } from 'react'

import { platforms, type PlatformId } from '@/lib/platforms'
import { megabytes } from '@/lib/release'
import { useRelease } from './ReleaseProvider'
import { usePlatform } from './usePlatform'

/** One button for the visitor's own system, the other systems beside it, and how to open the app
 *  the first time once a download has started. */
export default function HeroDownload() {
  const release = useRelease()
  const visitor = usePlatform()
  const [started, setStarted] = useState<PlatformId | null>(null)
  const own = platforms.find(({ id }) => id === visitor)
  const startedPlatform = platforms.find(({ id }) => id === started)

  return (
    <div className="hero-download">
      {own ? (
        <>
          <a className="button" href={release[own.id].url} onClick={() => setStarted(own.id)}>
            <DownloadIcon />
            Download for {own.name}
          </a>
          <p className="hero-download-meta">
            Version {release[own.id].version} · {megabytes(release[own.id].size)} · {own.needs}
          </p>
        </>
      ) : (
        <>
          <a className="button" href="#download">
            <DownloadIcon />
            {visitor === 'other' ? 'See the desktop downloads' : 'Download Colloquia'}
          </a>
          <p className="hero-download-meta">
            {visitor === 'other'
              ? 'Colloquia is a desktop app. Open this page on a Mac, Windows or Linux computer.'
              : 'Free for Mac, Windows and Linux'}
          </p>
        </>
      )}
      <p className="hero-download-others">
        {own ? 'Also for ' : 'Direct downloads: '}
        {platforms
          .filter(({ id }) => id !== own?.id)
          .map(({ id, name }, i) => (
            <span key={id}>
              {i > 0 && ' · '}
              <a href={release[id].url} onClick={() => setStarted(id)}>
                {name}
              </a>
            </span>
          ))}
      </p>
      <div role="status">
        {startedPlatform && (
          <div className="first-open">
            <p className="first-open-title">
              Your {startedPlatform.name} download has started. The first time you open it:
            </p>
            <ol>
              {startedPlatform.firstOpen.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </div>
  )
}

export function DownloadIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 4v11M7 10l5 5 5-5M5 20h14" />
    </svg>
  )
}
