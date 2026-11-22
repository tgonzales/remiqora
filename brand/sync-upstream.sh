#!/usr/bin/env bash
# Brings the latest upstream (inikolax/remiqora) into the current branch and checks the brand layer still holds.
#
#   brand/sync-upstream.sh            merge upstream's master
#   brand/sync-upstream.sh v0.7.0     merge an upstream tag
#
# The branch differs from upstream only by brand/, .github/README.md, the nix-* workflows and one line in
# frontend/vite.config.ts, so the merge is normally clean. A merge, never a rebase: the branch is published.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

UPSTREAM_URL=${UPSTREAM_URL:-https://github.com/inikolax/remiqora.git}
ref=${1:-master}

git remote get-url upstream >/dev/null 2>&1 || git remote add upstream "$UPSTREAM_URL"
git config rerere.enabled true   # a conflict resolved once is resolved the same way next time

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "uncommitted changes: commit them, or run 'node brand/apply.mjs --restore' if the brand was applied in place" >&2
  exit 1
fi

git fetch --tags upstream "$ref"
before=$(git rev-parse HEAD)
if ! git merge --no-edit FETCH_HEAD; then
  echo >&2
  echo "Conflicts. Outside brand/ the upstream side is almost always the one to keep; in frontend/vite.config.ts" >&2
  echo "keep upstream's version and put the nixBrand() import and plugin entry back. Then: git commit, and" >&2
  echo "run 'node brand/check.mjs'." >&2
  exit 1
fi

if [ "$before" = "$(git rev-parse HEAD)" ]; then echo "already up to date with upstream $ref"; exit 0; fi
echo "merged $(git rev-list --count "$before"..HEAD) commits from upstream $ref"
node brand/check.mjs
