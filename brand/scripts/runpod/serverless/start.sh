#!/usr/bin/env bash
# The worker's command. Points ACE-Step's checkpoints at the global volume (models are downloaded there on the
# very first start and reused by every worker after it), starts the health answer for the load balancer, then
# runs acestep-api the way the studio starts it locally (backend/app/config.py).
set -euo pipefail
PORT="${PORT:-8000}"
PORT_HEALTH="${PORT_HEALTH:-8080}"

if [[ -d /runpod-volume ]]; then DATA="${NIX_DATA_DIR:-/runpod-volume/nix-ace}"; else DATA="${NIX_DATA_DIR:-/opt/nix-data}"; fi
mkdir -p "$DATA/checkpoints" "$DATA/hf"
export HF_HOME="$DATA/hf"
if [[ ! -L "$ACE_DIR/checkpoints" ]]; then
  rm -rf "${ACE_DIR:?}/checkpoints"
  ln -s "$DATA/checkpoints" "$ACE_DIR/checkpoints"
fi

echo "[nix] ACE-Step $(head -c 12 /opt/nix/ace-version 2>/dev/null || echo '?'), models in $DATA, DiT ${ACE_DIT_MODEL:-acestep-v15-turbo}, LM ${ACE_LM_MODEL:-acestep-5Hz-lm-1.7B}"

# 204 while ACE-Step loads (or downloads) its models, 200 once its own /health answers
"$ACE_DIR/.venv/bin/python" /opt/nix/health.py "$PORT_HEALTH" "http://127.0.0.1:$PORT/health" &

cd "$ACE_DIR"
export ACESTEP_CONFIG_PATH="${ACE_DIT_MODEL:-acestep-v15-turbo}"
export ACESTEP_ON_DEMAND_MODEL_LOAD="${ACESTEP_ON_DEMAND_MODEL_LOAD:-true}"
exec uv run --no-sync acestep-api --host 0.0.0.0 --port "$PORT" --lm-model-path "${ACE_LM_MODEL:-acestep-5Hz-lm-1.7B}"
