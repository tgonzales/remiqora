#!/usr/bin/env bash
# Runs ON the pod: starts, stops and reports the ACE-Step API server, the same way the studio starts it locally
# (backend/app/config.py), bound to 127.0.0.1: the only way in is the SSH tunnel.
#
#   ace_engine.sh start [PORT] [DIT_MODEL] [LM_MODEL]     returns at once; `status` tells when it answers
#   ace_engine.sh stop | status | logs [LINES]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common.sh"

PID_FILE="$RUN_DIR/ace.pid"; LOG="$RUN_DIR/ace.log"; PORT_FILE="$RUN_DIR/ace.port"
running() { [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; }

case "${1:-status}" in
  start)
    port="${2:-8001}"; dit="${3:-acestep-v15-turbo}"; lm="${4:-acestep-5Hz-lm-1.7B}"
    [[ "$port" =~ ^[0-9]+$ ]] || die "port must be a number"
    [[ -d "$ACE_DIR/.venv" ]] || die "ACE-Step is not installed on this pod: run runpod-deploy.py first"
    if running && [[ "$(cat "$PORT_FILE" 2>/dev/null)" == "$port" ]]; then
      say "ACE-Step is already running on port $port"
    else
      stop_pidfile "$PID_FILE"
      ensure_system_tools
      if command -v nvidia-smi >/dev/null 2>&1 && ! nvidia-smi -L 2>/dev/null | grep -q GPU; then
        die "this pod started without a GPU (RunPod had none free on its machine): stop it and start it again later, or deploy a new pod"
      fi
      : > "$LOG"
      ( cd "$ACE_DIR" && ACESTEP_CONFIG_PATH="$dit" exec setsid uv run acestep-api --host 127.0.0.1 --port "$port" --lm-model-path "$lm" ) >>"$LOG" 2>&1 &
      echo $! > "$PID_FILE"; echo "$port" > "$PORT_FILE"
      say "ACE-Step starting on port $port ($dit, $lm)"
    fi
    "$HERE/idle_guard.sh" ensure
    ;;
  stop)
    if running; then stop_pidfile "$PID_FILE"; say "ACE-Step stopped"; else stop_pidfile "$PID_FILE"; say "ACE-Step was not running"; fi
    ;;
  status)
    version="$(head -n 1 "$NIX_HOME/ace-version" 2>/dev/null | cut -c1-12 || true)"
    if running; then
      port="$(cat "$PORT_FILE")"
      if curl -fsS -m 3 -o /dev/null "http://127.0.0.1:$port/health" 2>/dev/null; then echo "ace running port=$port version=${version:-unknown}"
      else echo "ace starting port=$port version=${version:-unknown}"; fi
    else
      echo "ace stopped version=${version:-not-installed}"
    fi
    ;;
  logs) tail -n "${2:-60}" "$LOG" 2>/dev/null || true ;;
  *) die "usage: ace_engine.sh start|stop|status|logs" ;;
esac
