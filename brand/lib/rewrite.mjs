// Pure text rewriting shared by the Vite plugin (frontend, dev and build) and apply.mjs (everything else).
import brand, { KEEP, NAME_RULE, rules } from '../brand.config.mjs'

const KEEP_RE = new RegExp(KEEP, 'g')

/**
 * Returns `text` with the brand applied for the file at `relPath` (repo-relative, forward slashes).
 * `hits`, when given, collects the ids of the rules that matched (check.mjs reads it).
 */
export function rewrite(text, relPath, hits) {
  let out = text
  for (const rule of rules) {
    if (!rule.files.test(relPath)) continue
    const next = typeof rule.find === 'string' ? out.split(rule.find).join(rule.replace) : out.replace(rule.find, rule.replace)
    if (next !== out) hits?.add(rule.id)
    out = next
  }
  out = out.replace(NAME_RULE, brand.name)
  return out.replace(KEEP_RE, brand.upstreamName)
}

/** Text files only: anything else is copied or left alone. */
export const TEXT_FILE = /\.(vue|ts|js|mjs|cjs|json|html|css|py|sh|bat|ps1|yml|yaml|md|txt)$/i
