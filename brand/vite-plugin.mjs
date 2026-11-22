// Applies the brand to the frontend, in `vite` (dev) and `vite build` alike, without editing upstream files:
//   - user-visible text and the few canvas colours, through brand/lib/rewrite.mjs;
//   - the theme, by importing brand/theme.css after upstream's style.css;
//   - the favicon, served from brand/assets;
//   - the sign-in gate of the cloud server (brand/auth/gate.ts), loaded in front of upstream's entry.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { rewrite, TEXT_FILE } from './lib/rewrite.mjs'

const brandDir = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(brandDir, '..')
const themeCss = path.join(brandDir, 'theme.css').split(path.sep).join('/')
const favicon = path.join(brandDir, 'assets', 'mark.svg')
const gate = path.join(brandDir, 'auth', 'gate.ts').split(path.sep).join('/')
// The gate needs @adaflow/sdk from brand/node_modules (`npm ci` in brand/). Upstream's own scripts build the
// page without that step; they are for local installs, which have no sign-in, so the build goes on without it.
const withGate = fs.existsSync(path.join(brandDir, 'node_modules', '@adaflow', 'sdk'))
const APP = 'nix-app' // query that marks upstream's entry module when it is loaded behind the sign-in gate

const rel = (file) => path.relative(repoRoot, file.split('?')[0]).split(path.sep).join('/')

export default function nixBrand() {
  let outDir = null
  return {
    name: 'nix-brand',
    enforce: 'pre',
    configResolved(config) {
      if (!withGate) config.logger.warn('nix-brand: brand/node_modules is missing, building WITHOUT the Adaflow sign-in screen (fine for a local install; a cloud server needs `npm ci` in brand/)')
      outDir = path.resolve(config.root, config.build.outDir)
    },
    transform(code, id) {
      const file = rel(id)
      if (!file.startsWith('frontend/src/') || !TEXT_FILE.test(file)) return null
      // A component's parts (App.vue?vue&type=script…) are cut from the component after it went through here.
      // A second pass would undo what the first one kept on purpose: the upstream name in the footer credit.
      if (/[?&]vue(&|$)/.test(id)) return null
      if (file === 'frontend/src/main.ts') {
        // The entry the page loads becomes the sign-in gate (brand/auth/gate.ts); upstream's own entry is
        // loaded behind it, unchanged, once the gate lets the app start. On a server without sign-in (the
        // desktop app, a local install) the gate answers at once.
        if (withGate && !new URLSearchParams(id.split('?')[1] ?? '').has(APP)) {
          return { code: `import gate from ${JSON.stringify(gate)}\ngate().then((ok) => { if (ok) import('./main.ts?${APP}') })\n`, map: null }
        }
        // the theme comes last, so its tokens win over upstream's @theme block
        return { code: `${rewrite(code, file)}\nimport ${JSON.stringify(themeCss)}\n`, map: null }
      }
      const out = rewrite(code, file)
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
