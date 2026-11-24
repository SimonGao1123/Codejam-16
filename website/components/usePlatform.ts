'use client'

import { useSyncExternalStore } from 'react'

import type { PlatformId } from '@/lib/platforms'

/** 'other' = a phone, tablet or a system the app has no download for; null = not known yet (the
 *  prerendered page, or JavaScript is off). */
export type Visitor = PlatformId | 'other' | null

export function detectPlatform(userAgent: string, touchPoints: number): Exclude<Visitor, null> {
  if (/Android|iPhone|iPad|iPod|CrOS/.test(userAgent)) return 'other'
  if (/Macintosh/.test(userAgent)) return touchPoints > 1 ? 'other' : 'mac' // iPads say Macintosh
  if (/Windows/.test(userAgent)) return 'windows'
  if (/Linux|X11/.test(userAgent)) return 'linux'
  return 'other'
}

const subscribe = () => () => {}

export function usePlatform(): Visitor {
  return useSyncExternalStore<Visitor>(
    subscribe,
    () => detectPlatform(navigator.userAgent, navigator.maxTouchPoints),
    () => null
  )
}
