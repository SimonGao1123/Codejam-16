'use client'

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

import { fetchRelease, type Release } from '@/lib/release'

const ReleaseContext = createContext<Release | null>(null)

export function ReleaseProvider({
  initial,
  children
}: {
  initial: Release // the newest release when the site was built
  children: ReactNode
}) {
  const [release, setRelease] = useState(initial)
  useEffect(() => {
    let cancelled = false
    void fetchRelease(initial).then((latest) => {
      if (!cancelled) setRelease(latest)
    })
    return () => {
      cancelled = true
    }
  }, [initial])
  return <ReleaseContext value={release}>{children}</ReleaseContext>
}

export function useRelease(): Release {
  const release = useContext(ReleaseContext)
  if (!release) throw new Error('useRelease needs a ReleaseProvider')
  return release
}
