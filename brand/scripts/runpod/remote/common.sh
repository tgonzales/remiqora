# Sourced by the scripts that run ON the pod. Everything lives under /workspace (the pod's volume): the
# container disk is wiped whenever the pod is stopped, the volume is not.
NIX_HOME="${NIX_HOME:-/workspace/nix}"
ACE_DIR="$NIX_HOME/ACE-Step-1.5"
RUN_DIR="$NIX_HOME/run"
export UV_CACHE_DIR="$NIX_HOME/uv-cache"
export UV_PYTHON_INSTALL_DIR="$NIX_HOME/uv-python"
export HF_HOME="$NIX_HOME/hf"
export HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 PYTHONUTF8=1
export PATH="$NIX_HOME/bin:$PATH"
mkdir -p "$NIX_HOME/bin" "$RUN_DIR"

say() { printf '[nix] %s\n' "$*"; }
die() { printf '[nix] error: %s\n' "$*" >&2; exit 1; }

# git, ffmpeg and curl come from apt, so they are gone after every stop/start of the pod: put them back.
# uv is installed onto the volume and stays.
ensure_system_tools() {
  local missing=()
  for tool in git ffmpeg curl; do command -v "$tool" >/dev/null 2>&1 || missing+=("$tool"); done
  if (( ${#missing[@]} )); then
    say "installing ${missing[*]}"
    export DEBIAN_FRONTEND=noninteractive
    { apt-get update -qq && apt-get install -y -qq "${missing[@]}"; } >/dev/null || die "apt-get could not install: ${missing[*]}"
  fi
  if ! command -v uv >/dev/null 2>&1; then
    say "installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="$NIX_HOME/bin" sh >/dev/null 2>&1 || die "uv could not be installed"
  fi
}

# wait_health URL SECONDS [PID]: 0 when URL answers, 1 on timeout or when PID died first
wait_health() {
  local url="$1" deadline=$(( $(date +%s) + $2 )) pid="${3:-}"
  while (( $(date +%s) < deadline )); do
    curl -fsS -m 3 -o /dev/null "$url" 2>/dev/null && return 0
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then return 1; fi
    sleep 2
  done
  return 1
}

# stop_pidfile FILE: ends the process group started with setsid, politely, then for good
stop_pidfile() {
  local file="${1:?}" pid
  [[ -f "$file" ]] || return 0
  pid="$(cat "$file" 2>/dev/null || true)"
  rm -f -- "${file:?}"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  kill -0 "$pid" 2>/dev/null || return 0
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || return 0; sleep 0.5; done
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
}
