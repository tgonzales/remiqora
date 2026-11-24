#!/usr/bin/env bash
# Runs ON the pod: installs ACE-Step 1.5 with the studio's patch.
#
#   ace_bootstrap.sh PATCH PINNED_REF [latest|pinned]
#
# "latest" (the default) tries ACE-Step's newest commit first. The studio talks to ACE-Step through routes the
# patch adds, so a commit is only kept when the patch applies to it, its environment installs and its API server
# answers. If any of that fails, the commit the studio was tested with (PINNED_REF, from upstream's
# setup_linux.sh) is installed instead, and the reason is printed. Run it again any time to update.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common.sh"

PATCH="${1:?patch file}"; PINNED="${2:?pinned ref}"; MODE="${3:-latest}"
REPO_URL="${ACE_REPO_URL:-https://github.com/ace-step/ACE-Step-1.5.git}"
SMOKE_PORT="${ACE_SMOKE_PORT:-8009}"
SMOKE_TIMEOUT="${ACE_SMOKE_TIMEOUT:-1500}"   # the first start downloads the models (several GB)
[[ -f "$PATCH" ]] || die "patch not found: $PATCH"

ensure_system_tools
"$HERE/ace_engine.sh" stop >/dev/null 2>&1 || true   # never rebuild under a running engine

if [[ ! -d "$ACE_DIR/.git" ]]; then
  say "cloning $REPO_URL"
  git clone -q "$REPO_URL" "$ACE_DIR"
fi
git -C "$ACE_DIR" fetch -q --prune origin
# the remote's default branch, whatever it is called
latest="$(git -C "$ACE_DIR" ls-remote origin HEAD | cut -f1)"
[[ -n "$latest" ]] || die "could not read ACE-Step's latest commit"

# install REF: 0 when ACE-Step at REF, patched, installs and its API answers; otherwise 1 and the reason in $why
why=""
install() {
  local ref="$1" log="$RUN_DIR/bootstrap-${1:0:12}.log" rel
  # files the patch adds are untracked: remove them, or a second apply fails with "already exists"
  while IFS= read -r rel; do
    [[ -n "$rel" && "$rel" != /* && "$rel" != *..* ]] && rm -f -- "${ACE_DIR:?}/${rel:?}"
  done < <(awk '/^diff --git /{f=substr($4,3); n=NR; next} NR==n+1 && /^new file mode/{print f}' "$PATCH")
  if ! git -C "$ACE_DIR" -c advice.detachedHead=false checkout -q --force --detach "$ref" 2>>"$log"; then
    why="commit $ref not found"; return 1
  fi
  if ! git -C "$ACE_DIR" apply --check --whitespace=nowarn "$PATCH" 2>>"$log"; then
    why="the studio's patch does not apply to it"; return 1
  fi
  git -C "$ACE_DIR" apply --whitespace=nowarn "$PATCH"
  say "installing the Python environment for ${ref:0:12} (PyTorch for CUDA: this takes a while)"
  if ! (cd "$ACE_DIR" && uv sync) >>"$log" 2>&1; then
    why="its Python environment did not install (see $log)"; return 1
  fi
  say "starting its API once to check it (the first start downloads the models)"
  ( cd "$ACE_DIR" && exec setsid uv run acestep-api --host 127.0.0.1 --port "$SMOKE_PORT" ) >>"$log" 2>&1 &
  local pid=$! ok=0
  echo "$pid" > "$RUN_DIR/smoke.pid"
  wait_health "http://127.0.0.1:$SMOKE_PORT/health" "$SMOKE_TIMEOUT" "$pid" || ok=1
  stop_pidfile "$RUN_DIR/smoke.pid"
  if (( ok != 0 )); then why="its API server did not come up (see $log)"; return 1; fi
  return 0
}

installed="" kind=""
if [[ "$MODE" == latest ]]; then
  say "ACE-Step's latest commit is ${latest:0:12}"
  if install "$latest"; then
    installed="$latest"; kind="latest"
  else
    say "the latest commit cannot be used: $why"
    say "falling back to the tested commit $PINNED"
  fi
fi
if [[ -z "$installed" ]]; then
  fallback_reason="$why"
  install "$PINNED" || die "the tested commit $PINNED failed too: $why"
  installed="$(git -C "$ACE_DIR" rev-parse HEAD)"; kind="pinned"
  [[ "$MODE" == latest ]] && kind="pinned (latest ${latest:0:12} refused: $fallback_reason)"
fi

printf '%s\n%s\n%s\n' "$installed" "$kind" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$NIX_HOME/ace-version"
say "ACE-Step ready: ${installed:0:12} [$kind]"
