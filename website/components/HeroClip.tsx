'use client'

import Image from 'next/image'
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'

import { screens } from '@/lib/screens'

const query = '(prefers-reduced-motion: reduce)'
const subscribe = (onChange: () => void) => {
  const media = window.matchMedia(query)
  media.addEventListener('change', onChange)
  return () => media.removeEventListener('change', onChange)
}

const ALT =
  'The Colloquia practice page during a session: the presenter on camera with an alert on the video, beside the session timer.'

/** A silent loop of a real practice session; a still image without JavaScript or with reduced
 *  motion. */
export default function HeroClip() {
  const still = useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => true
  )
  const video = useRef<HTMLVideoElement>(null)
  const [paused, setPaused] = useState(false)

  useEffect(() => {
    if (paused) video.current?.pause()
    else video.current?.play().catch(() => {}) // autoplay not allowed: the poster stays
  }, [paused, still])

  if (still) return <Image className="hero-media" src={screens.hero} alt={ALT} priority />
  return (
    <div className="hero-clip">
      <video
        ref={video}
        className="hero-media"
        width={screens.hero.width}
        height={screens.hero.height}
        poster={screens.hero.src}
        muted
        loop
        playsInline
        autoPlay
        aria-label={ALT}
      >
        <source src="/practice.webm" type="video/webm" />
        <source src="/practice.mp4" type="video/mp4" />
      </video>
      <button
        type="button"
        className="hero-pause"
        aria-pressed={paused}
        onClick={() => setPaused((value) => !value)}
      >
        {paused ? 'Play' : 'Pause'}
      </button>
    </div>
  )
}
