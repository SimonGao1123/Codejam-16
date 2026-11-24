'use client'

import { platforms } from '@/lib/platforms'
import { megabytes } from '@/lib/release'
import { DownloadIcon } from './HeroDownload'
import { useRelease } from './ReleaseProvider'
import { usePlatform } from './usePlatform'

export default function Downloads() {
  const release = useRelease()
  const visitor = usePlatform()
  return (
    <div className="downloads">
      {platforms.map(({ id, name, needs, untested, firstOpen }) => (
        <section className="card download" key={id} aria-labelledby={`download-${id}`}>
          <header className="card-head">
            <h3 id={`download-${id}`}>{name}</h3>
            <span className="chips">
              {visitor === id && <span className="chip chip-accent">Your system</span>}
              {untested && <span className="chip chip-warn">Not tested yet</span>}
            </span>
          </header>
          <p className="download-needs">{needs}</p>
          <a className="button" href={release[id].url}>
            <DownloadIcon />
            Download for {name}
          </a>
          <p className="download-meta">
            Version {release[id].version} · {megabytes(release[id].size)}
          </p>
          <h4>First time opening it</h4>
          <ol>
            {firstOpen.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  )
}
