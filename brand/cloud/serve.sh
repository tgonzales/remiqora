#!/usr/bin/env bash
# Starts NiX Studio as a cloud server with Adaflow sign-in. Run upstream's setup_linux.sh once before (engines,
# models, backend/.venv); this adds what the sign-in needs, builds the page and serves it.
#
#   NIX_PUBLIC_URL=https://nix-studio.adalink.app NIX_SSO_SESSION_SECRET=<long random> brand/cloud/serve.sh
#
# Put a TLS-terminating reverse proxy in front of it: the server itself speaks plain http on HOST:PORT.
# Settings (all optional except the two above in production): see brand/cloud/README.md.
set -euo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

HOST=${REMIQORA_HOST:-127.0.0.1}
PORT=${REMIQORA_PORT:-9000}
PY=backend/.venv/bin/python3

[ -x "$PY" ] || { echo "backend/.venv is missing: run ./setup_linux.sh first" >&2; exit 1; }
if [ -z "${NIX_SSO_SESSION_SECRET:-}" ]; then
  echo "warning: NIX_SSO_SESSION_SECRET is not set; everybody is signed out whenever the server restarts" >&2
fi

"$PY" -m pip install -q -r backend/nix/requirements.txt
(cd brand && npm ci --no-audit --no-fund)
(cd frontend && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run build)

cd backend
# one worker: the upstream app keeps its job queue and GPU processes in this process's memory
NIX_SSO=1 exec .venv/bin/python3 -m uvicorn nix.main:app --host "$HOST" --port "$PORT" --no-access-log \
  --proxy-headers --forwarded-allow-ips "${NIX_TRUSTED_PROXIES:-127.0.0.1}"
