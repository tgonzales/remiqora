"""`acestep-api`, as the studio calls it, turned into "connect to ACE-Step on the pod"."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

# uv installs this project in editable mode, so this file is the one in the repository
MODELS_SCRIPT = Path(__file__).resolve().parents[4] / "runpod-models.py"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default="8001")
    parser.add_argument("--lm-model-path", default="acestep-5Hz-lm-1.7B")
    args, _ = parser.parse_known_args()
    if not MODELS_SCRIPT.is_file():
        sys.exit(f"{MODELS_SCRIPT} is missing: this folder must stay inside the NiX Studio repository")

    command = [
        sys.executable, str(MODELS_SCRIPT), "connect", "ace", "--port", args.port, "--lm", args.lm_model_path,
        # the studio passes its chosen DiT model the way ACE-Step reads it
        "--dit", os.environ.get("ACESTEP_CONFIG_PATH", "acestep-v15-turbo"),
        *(["--stop-pod"] if os.environ.get("NIX_RUNPOD_STOP_POD", "").lower() in ("1", "true", "yes") else []),
    ]
    child = subprocess.Popen(command)

    def forward(signum: int, _frame: object) -> None:
        child.send_signal(signal.SIGTERM if signum != signal.SIGINT else signal.SIGINT)

    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), forward)
    sys.exit(child.wait())
