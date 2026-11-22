#!/usr/bin/env node
// Applies the brand to what Vite does not build: the desktop shell and its first-run screen, the installer
// config, the backend's visible strings and the install/launch scripts. It rewrites those files IN PLACE, so it
// is meant for a throwaway checkout: CI, right before `npm run dist` in desktop/ (see .github/workflows/nix-desktop.yml).
//
//   node brand/apply.mjs            apply (refuses when the target files have uncommitted changes)
//   node brand/apply.mjs --restore  put the upstream files back (git checkout)
//
// The result is never committed: the branch keeps upstream's files byte for byte, which is what keeps merges clean.
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { applyTargets, assets, styleAppends } from './brand.config.mjs'
import { rewrite, TEXT_FILE } from './lib/rewrite.mjs'

const brandDir = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(brandDir, '..')
const git = (...args) => execFileSync('git', args, { cwd: root, encoding: 'utf8' })

const touched = [...applyTargets, ...assets.map(([, to]) => to), ...styleAppends.map(([, to]) => to)]

if (process.argv.includes('--restore')) {
  git('checkout', '--', ...touched)
  console.log('upstream files restored')
  process.exit(0)
}

const dirty = git('status', '--porcelain', '--', ...touched).trim()
if (dirty && !process.argv.includes('--force')) {
  console.error(`these files have uncommitted changes; commit them, or run with --restore first:\n${dirty}`)
  process.exit(1)
}

let changed = 0
for (const file of git('ls-files', '-z', '--', ...applyTargets).split('\0').filter(Boolean)) {
  if (!TEXT_FILE.test(file)) continue
  const abs = path.join(root, file)
  const before = fs.readFileSync(abs, 'utf8')
  const after = rewrite(before, file)
  if (after !== before) { fs.writeFileSync(abs, after); changed++ }
}
for (const [from, to] of styleAppends) fs.appendFileSync(path.join(root, to), fs.readFileSync(path.join(brandDir, from), 'utf8'))
for (const [from, to] of assets) fs.copyFileSync(path.join(brandDir, 'assets', from), path.join(root, to))

console.log(`brand applied: ${changed} files rewritten, ${styleAppends.length} stylesheets extended, ${assets.length} assets replaced`)
console.log('do not commit this state; `node brand/apply.mjs --restore` undoes it')
