// The NiX Studio brand layer. Everything that turns the upstream app (Remiqora) into NiX Studio is described
// here and applied at build time; the upstream sources stay untouched so that merges from upstream stay clean.
//
// Only what a person sees changes. File names, functions, environment variables (REMIQORA_*), localStorage keys
// and i18n keys keep their upstream names: the word rule below only matches the capitalised product name standing
// on its own, never an identifier such as RemiqoraConsole or remiqora_locale.

export const KEEP = '' // stands for the upstream name where it must survive (attribution); restored last

const brand = {
  name: 'NiX Studio',
  upstreamName: 'Remiqora',
  appId: 'app.adalink.nix-studio',
  dataFolder: 'NiXStudio', // default data root: %LOCALAPPDATA%\\NiXStudio, ~/Library/Application Support/NiXStudio

  urls: {
    site: 'https://nix-studio.adalink.app',
    issues: 'https://github.com/tgonzales/remiqora/issues/new',
  },
}

// The N of the mark, drawn in the same 24x24 box and stroke as upstream's R.
const MARK_R = 'M7 19V5h5.5a3.75 3.75 0 0 1 0 7.5H7M12.5 12.5 17 19'
const MARK_N = 'M7 19V5l10 14V5'

/**
 * Rules run in order on every file whose repo-relative path matches `files`, before the general name rule.
 * `required` rules must match at least once: when upstream rewrites the line, `npm run check` (brand/check.mjs)
 * fails and names the rule, instead of the old brand quietly coming back.
 */
export const rules = [
  // --- frontend: the mark -------------------------------------------------------------------------------------
  { id: 'header-mark', files: /^frontend\/src\/components\/shared\/AppHeader\.vue$/, find: MARK_R, replace: MARK_N, required: true },
  { id: 'footer-mark', files: /^frontend\/src\/components\/shared\/AppFooter\.vue$/, find: MARK_R, replace: MARK_N, required: true },

  // --- frontend: attribution (MIT: upstream's name, author and licence stay visible) ----------------------------
  {
    id: 'footer-credit', required: true,
    files: /^frontend\/src\/components\/shared\/AppFooter\.vue$/,
    find: '<span class="font-medium text-text">Remiqora</span> · ©',
    replace: `<span class="font-medium text-text">${brand.name}</span> · based on ${KEEP} · ©`,
  },
  {
    id: 'footer-licence-row', required: true,
    files: /^frontend\/src\/components\/shared\/AppFooter\.vue$/,
    find: "{ name: 'Remiqora', license: 'MIT'",
    replace: `{ name: '${KEEP}', license: 'MIT'`,
  },

  // --- frontend: colours drawn on canvases, which CSS tokens cannot reach ----------------------------------------
  { id: 'waveform-from', required: true, files: /^frontend\/src\/components\/shared\/WaveformPlayer\.vue$/, find: "'#a855f7'", replace: "'#b9722a'" },
  { id: 'waveform-to', required: true, files: /^frontend\/src\/components\/shared\/WaveformPlayer\.vue$/, find: "'#ec4899'", replace: "'#a9512b'" },
  { id: 'clip-rms', required: true, files: /^frontend\/src\/components\/editor\/TimelineClip\.vue$/, find: 'rgba(216, 180, 254, 0.9)', replace: 'rgba(233, 194, 125, 0.9)' },
  { id: 'clip-midi', required: true, files: /^frontend\/src\/components\/editor\/TimelineClip\.vue$/, find: "'#6366f1'", replace: "'#b9722a'" },
  // the piano-roll canvas and its frame: upstream's blue-black becomes the warm panel tone
  { id: 'midi-bg', required: true, files: /^frontend\/src\/(audio\/miniMidiPlayer\.ts|components\/editor\/PianoRoll\.vue|components\/shared\/MidiPanel\.vue)$/, find: '#161922', replace: '#1a1510' },
  { id: 'midi-grid', required: true, files: /^frontend\/src\/audio\/miniMidiPlayer\.ts$/, find: '#232936', replace: '#2e251c' },

  // --- desktop ----------------------------------------------------------------------------------------------------
  { id: 'desktop-app-id', required: true, files: /^desktop\/electron-builder\.yml$/, find: 'io.github.inikolax.remiqora', replace: brand.appId },
  // installer file names carry no space: NiX-Studio-Setup-0.6.0.exe
  { id: 'desktop-artifacts', required: true, files: /^desktop\/electron-builder\.yml$/, find: /Remiqora-(?=Setup|\$\{version\})/g, replace: 'NiX-Studio-' },
  // The data root (models, engines, projects) gets a name without a space: Python environments and native builds
  // live under it, and on Windows a space in that path is a risk upstream's own name never exercised.
  { id: 'desktop-data-root', required: true, files: /^desktop\/src\/paths\.js$/, find: "'Remiqora')", replace: `'${brand.dataFolder}')` },
  // a picked data folder already called like the app is used as it is, not nested once more
  {
    id: 'desktop-data-folder', required: true, files: /^desktop\/src\/main\.js$/,
    find: "=== 'remiqora' ? picked : path.join(picked, 'Remiqora')",
    replace: `=== '${brand.dataFolder.toLowerCase()}' ? picked : path.join(picked, '${brand.dataFolder}')`,
  },
  { id: 'script-data-root', required: true, files: /^scripts\/sa3_from_comfy\.py$/, find: '/ "Remiqora"', replace: `/ "${brand.dataFolder}"` },
  { id: 'script-data-root-doc', required: true, files: /^scripts\/sa3_from_comfy\.py$/, find: /(?<=LOCALAPPDATA%\\\\|Support\/)Remiqora/g, replace: brand.dataFolder },
  // "Report a problem" must not send reports about this build to the upstream project
  { id: 'desktop-issues', required: true, files: /^desktop\/src\/main\.js$/, find: 'https://github.com/inikolax/remiqora/issues/new', replace: brand.urls.issues },
]

/** The product name on its own: not inside an identifier, a lower-case key or a URL path segment. */
export const NAME_RULE = /(?<![A-Za-z0-9_])Remiqora(?![A-Za-z0-9_])/g

/** Files the brand is applied to outside Vite (see apply.mjs): git pathspecs, relative to the repo root. */
export const applyTargets = [
  'desktop/renderer', 'desktop/src', 'desktop/electron-builder.yml',
  'backend/app', 'backend/run.sh', 'backend/run.bat',
  'scripts', '*.sh', '*.bat', '*.ps1',
]

/** Brand files copied over upstream ones: [from (in brand/assets), to (repo-relative)]. */
export const assets = [
  ['mark.svg', 'frontend/public/favicon.svg'],
  ['mark.svg', 'desktop/renderer/logo.svg'],
  ['icon.png', 'desktop/build/icon.png'],
]

/** CSS appended to an upstream stylesheet: [from (in brand/), to (repo-relative)]. */
export const styleAppends = [
  ['desktop-theme.css', 'desktop/renderer/style.css'],
]

/** Where the upstream name may still appear in a finished build (attribution only). */
export const allowedUpstreamMentions = [
  /based on Remiqora/,
  /name:\s*["'`]Remiqora["'`],\s*license/,
]

export default brand
