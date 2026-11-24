import Image, { type StaticImageData } from 'next/image'
import type { ReactNode } from 'react'

type Props = {
  title: string
  screen: StaticImageData
  alt: string
  wide?: boolean // takes the full width, with the text beside the screenshot
  children: ReactNode
}

export default function Feature({ title, screen, alt, wide, children }: Props) {
  return (
    <article className="feature" data-wide={wide || undefined}>
      <div className="feature-text">
        <h3>{title}</h3>
        {children}
      </div>
      <div className="feature-screen">
        <Image src={screen} alt={alt} sizes={wide ? '(min-width: 900px) 740px, 100vw' : '560px'} />
      </div>
    </article>
  )
}
