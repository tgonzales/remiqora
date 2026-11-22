# The NiX Studio brand layer

NiX Studio is [Remiqora](https://github.com/inikolax/remiqora) with another name and look. Upstream is very
active, and the product name sits in the files it changes most (39 translation files), so this branch does not
edit upstream's files: the brand is described here and applied at build time. What the branch adds to upstream:

| | |
|---|---|
| `brand/` | this folder |
| `frontend/vite.config.ts` | one import and one plugin entry: the only upstream file that is edited |
| `.github/README.md` | the page GitHub shows instead of upstream's README |
| `.github/workflows/nix-*.yml` | brand check, installers, weekly sync |

Only what a person sees changes. File names, functions, environment variables (`REMIQORA_*`), `localStorage`
keys and i18n keys keep their upstream names.

## What is where

| File | What it does |
|---|---|
| `brand.config.mjs` | The name, the links, and the rules: which lines change beyond the plain name swap. |
| `theme.css` | The app's colours (earth tones, brass and copper): overrides upstream's theme tokens. |
| `desktop-theme.css` | The same colours for the desktop first-run screen. |
| `assets/` | `mark.svg` (favicon and logo), `icon.svg` → `icon.png` (app icon; `node brand/render-icon.cjs`). |
| `vite-plugin.mjs` | Applies all of it to the frontend, in `npm run dev` and `npm run build` alike. |
| `apply.mjs` | Applies it, in place, to what Vite does not build: desktop shell, installer config, backend strings, scripts. CI only. |
| `check.mjs` | Fails when a rule no longer finds its line, or a build still shows the upstream name. |
| `sync-upstream.sh` | Merges upstream and runs the check. |

## Everyday use

```sh
cd frontend && npm run dev          # the app as NiX Studio; nothing to do
node brand/check.mjs                # after a merge from upstream
node brand/check.mjs --dist frontend/dist   # after a build
```

Desktop installers are built by the "NiX desktop app" workflow (run it by hand, or push a tag such as
`nix-v0.6.0-1`). To build one locally:

```sh
node brand/apply.mjs                # rewrites upstream files in place: do not commit this state
(cd desktop && npm run dist)
node brand/apply.mjs --restore
```

## Following upstream

```sh
brand/sync-upstream.sh              # upstream's master
brand/sync-upstream.sh v0.7.0       # or a tag
```

The "NiX upstream sync" workflow does the same every Monday and opens a pull request. It needs a `SYNC_TOKEN`
secret (contents, pull requests and workflows: write), and scheduled workflows only run from the repository's
default branch, so make the product branch the default one.

When the check fails after a merge, upstream rewrote a line a rule depended on: the message names the rule; fix
its `find` in `brand.config.mjs`. A warning about an accent colour means upstream wrote a colour into a component
instead of using a theme token: add a rule for it.

## Kept from upstream on purpose

- The footer credit ("based on Remiqora", the author's copyright) and the licence table: the MIT licence asks for it.
- `LICENSE`, `README.md` and the other upstream documents.
- The Hugging Face sign-in in the desktop app identifies itself with upstream's OAuth client
  (`remiqora.com/.well-known/oauth-cimd`), so Hugging Face's consent page names Remiqora. Changing it means
  hosting a client metadata document of our own and adding a rule for `desktop/src/hfLogin.js`.
