import { Analytics } from '@vercel/analytics/next'
import { SpeedInsights } from '@vercel/speed-insights/next'
import type { Metadata, Viewport } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'
import type { ReactNode } from 'react'

import './globals.css'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })
const mono = JetBrains_Mono({ subsets: ['latin'], variable: '--font-jetbrains' })

// Vercel sets this while building; elsewhere only the social preview image is affected
const host = process.env.VERCEL_PROJECT_PRODUCTION_URL
const description =
  'Colloquia is a free desktop app for rehearsing presentations. It watches your posture, listens to your voice and follows your script, and tells you what to fix.'

export const metadata: Metadata = {
  metadataBase: new URL(host ? `https://${host}` : 'http://localhost:3000'),
  title: 'Colloquia: rehearse presentations with live feedback',
  description,
  openGraph: {
    type: 'website',
    siteName: 'Colloquia',
    title: 'Rehearse like someone’s watching',
    description,
    images: [
      { url: '/og.jpg', width: 1200, height: 630, alt: 'Colloquia during a practice session' }
    ]
  },
  twitter: { card: 'summary_large_image' }
}

export const viewport: Viewport = { themeColor: '#0b0c0e', colorScheme: 'dark' }

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body>
        {children}
        <Analytics />
        <SpeedInsights />
      </body>
    </html>
  )
}
