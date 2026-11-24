#!/usr/bin/env bash
# Runs ON the pod: stops the pod when nobody has been connected for a while, so a forgotten pod does not keep
# charging for its GPU. The studio's tunnel touches $RUN_DIR/heartbeat every 30 seconds while it is open; when
# the heartbeat is older than the limit (run/idle-minutes, default 20; 0 turns the guard off) the engine is
# stopped and the pod stops itself with RunPod's own CLI, which official images carry. The volume, and so the
# install, stays.
#
#   idle_guard.sh ensure     start the guard unless it runs already
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common.sh"

IDLE_MINUTES="$(cat "$RUN_DIR/idle-minutes" 2>/dev/null || echo 20)"
PID_FILE="$RUN_DIR/idle-guard.pid"; HEARTBEAT="$RUN_DIR/heartbeat"
[[ "$IDLE_MINUTES" =~ ^[0-9]+$ ]] || die "idle minutes must be a number"

case "${1:-}" in
  ensure)
    stop_pidfile "$PID_FILE"            # restart it, so a changed limit takes effect
    (( IDLE_MINUTES == 0 )) && exit 0
    touch "$HEARTBEAT"                  # a fresh start counts as activity
    setsid nohup "$HERE/idle_guard.sh" loop >>"$RUN_DIR/idle-guard.log" 2>&1 < /dev/null &
    echo $! > "$PID_FILE"
    ;;
  loop)
    while sleep "${NIX_IDLE_TICK:-60}"; do
      age=$(( $(date +%s) - $(stat -c %Y "$HEARTBEAT" 2>/dev/null || echo 0) ))
      (( age < IDLE_MINUTES * 60 )) && continue
      say "$(date -u +%FT%TZ) no connection for $(( age / 60 )) min: stopping the engine and the pod"
      "$HERE/ace_engine.sh" stop || true
      if [[ -n "${RUNPOD_POD_ID:-}" ]] && command -v runpodctl >/dev/null 2>&1; then
        runpodctl pod stop "$RUNPOD_POD_ID" || say "runpodctl could not stop the pod: it keeps running and charging"
      else
        say "cannot stop the pod from inside (no runpodctl or RUNPOD_POD_ID): it keeps running and charging"
      fi
      exit 0
    done
    ;;
  *) die "usage: idle_guard.sh ensure" ;;
esac
