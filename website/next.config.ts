import type { NextConfig } from 'next'

const config: NextConfig = {
  output: 'export',
  images: { unoptimized: true }, // static export: images are already sized and compressed
  turbopack: { root: __dirname } // the repo has other lockfiles above and beside this folder
}

export default config
