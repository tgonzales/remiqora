// Applies the brand to the frontend, in `vite` (dev) and `vite build` alike, without editing upstream files:
//   - user-visible text and the few canvas colours, through brand/lib/rewrite.mjs;
//   - the theme, by importing brand/theme.css after upstream's style.css;
//   - the favicon, served from brand/assets.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { rewrite, TEXT_FILE } from './lib/rewrite.mjs'

const brandDir = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(brandDir, '..')
const themeCss = path.join(brandDir, 'theme.css').split(path.sep).join('/')
const favicon = path.join(brandDir, 'assets', 'mark.svg')

const rel = (file) => path.relative(repoRoot, file.split('?')[0]).split(path.sep).join('/')

export default function nixBrand() {
  let outDir = null
  return {
    name: 'nix-brand',
    enforce: 'pre',
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir)
    },
    transform(code, id) {
      const file = rel(id)
      if (!file.startsWith('frontend/src/') || !TEXT_FILE.test(file)) return null
      let out = rewrite(code, file)
      // the theme comes last, so its tokens win over upstream's @theme block
      if (file === 'frontend/src/main.ts') out += `\nimport ${JSON.stringify(themeCss)}\n`
      return out === code ? null : { code: out, map: null }
    },
    transformIndexHtml(html) {
      return rewrite(html, 'frontend/index.html')
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url?.split('?')[0] !== '/favicon.svg') return next()
        res.setHeader('Content-Type', 'image/svg+xml')
        res.end(fs.readFileSync(favicon))
      })
    },
    closeBundle() {
      if (outDir && fs.existsSync(outDir)) fs.copyFileSync(favicon, path.join(outDir, 'favicon.svg'))
    },
  }
}
