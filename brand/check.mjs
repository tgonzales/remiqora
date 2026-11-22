#!/usr/bin/env node
// Guards the brand layer against upstream drift. Run after every merge from upstream and in CI.
//
//   node brand/check.mjs                 every `required` rule of brand.config.mjs still finds its line
//   node brand/check.mjs --dist <dir>    also: no upstream name left in a finished build, attribution aside
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import brand, { allowedUpstreamMentions, applyTargets, NAME_RULE, rules } from './brand.config.mjs'
import { rewrite, TEXT_FILE } from './lib/rewrite.mjs'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const files = execFileSync('git', ['ls-files', '-z', '--', 'frontend/src', 'frontend/index.html', ...applyTargets], { cwd: root, encoding: 'utf8' })
  .split('\0').filter((f) => f && TEXT_FILE.test(f))

const problems = []

// 1. every required rule matched somewhere
const hits = new Set()
for (const file of files) rewrite(fs.readFileSync(path.join(root, file), 'utf8'), file, hits)
for (const rule of rules) {
  if (rule.required && !hits.has(rule.id)) problems.push(`rule "${rule.id}" no longer matches anything (${rule.files}): upstream changed that line, update brand/brand.config.mjs`)
}

// 2. upstream's accent colours written out in a component instead of taken from the theme tokens
const OLD_ACCENTS = /#a855f7|#ec4899|#d8b4fe|#c084fc/gi
// Track colours and per-engine chart colours are a fixed, named palette the user picks from, not the brand.
const PALETTES = /^frontend\/src\/(utils\/trackColors\.ts|utils\/dawExport\.ts|components\/shared\/ResourceMeters\.vue|components\/editor\/(AiPartPanel|SfxPanel|EditorHelpModal)\.vue|views\/editor\/ProjectsListPage\.vue|style\.css)$/
const warnings = []
for (const file of files.filter((f) => f.startsWith('frontend/src/') && !PALETTES.test(f))) {
  const left = rewrite(fs.readFileSync(path.join(root, file), 'utf8'), file).match(OLD_ACCENTS)
  if (left) warnings.push(`${file}: upstream accent colour written out (${[...new Set(left)].join(', ')}); add a rule or list the file as a palette`)
}

// 3. a finished build
const distArg = process.argv.indexOf('--dist')
if (distArg !== -1) {
  const dist = path.resolve(process.argv[distArg + 1] ?? '')
  if (!fs.existsSync(dist)) problems.push(`--dist: ${dist} does not exist`)
  else {
    const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => e.isDirectory() ? walk(path.join(dir, e.name)) : [path.join(dir, e.name)])
    let named = 0
    for (const file of walk(dist).filter((f) => TEXT_FILE.test(f))) {
      const text = fs.readFileSync(file, 'utf8')
      if (text.includes(brand.name)) named++
      for (const m of text.matchAll(NAME_RULE)) {
        const around = text.slice(Math.max(0, m.index - 40), m.index + 60).replace(/\s+/g, ' ')
        if (!allowedUpstreamMentions.some((re) => re.test(around))) problems.push(`${path.relative(root, file)}: "${brand.upstreamName}" is still visible: …${around}…`)
      }
    }
    if (!named) problems.push(`--dist: "${brand.name}" appears nowhere in ${dist}: the brand was not applied to this build`)
  }
}

for (const w of warnings) console.warn(`warning: ${w}`)
if (problems.length) {
  for (const p of problems) console.error(`error: ${p}`)
  process.exit(1)
}
console.log(`brand check passed: ${rules.filter((r) => r.required).length} required rules match, ${files.length} files scanned${distArg !== -1 ? ', build is clean' : ''}`)
