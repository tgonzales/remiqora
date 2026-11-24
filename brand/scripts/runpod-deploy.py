#!/usr/bin/env python3
"""Puts ACE-Step 1.5 on a RunPod GPU pod, for a studio that runs on this computer.

    export RUNPOD_API_KEY=...
    python brand/scripts/runpod-deploy.py                 # create the pod (asks first), install, stop the pod
    python brand/scripts/runpod-deploy.py                 # again later: update ACE-Step on the same pod
    python brand/scripts/runpod-deploy.py --ace pinned    # the commit the studio was tested with, no attempt at the latest

The pod runs only the engine. Nothing on it is public: its API listens on 127.0.0.1 and is reached through an
SSH tunnel that runpod-models.py opens. Each run installs ACE-Step's latest commit when the studio's patch
applies to it and its API comes up, and the tested commit otherwise.

When it is done the pod is stopped (use --keep-running to leave it on): a stopped pod costs only its disk.
See brand/scripts/README.md.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "runpod"))
import lib  # noqa: E402
from lib import ApiError, api, die, say  # noqa: E402

SHIM = lib.HERE / "shim" / "ace-step"


def create_pod(args: argparse.Namespace) -> dict:
    public_key = Path(f"{lib.ssh_key()}.pub").read_text(encoding="utf-8").strip()
    body = {
        "name": args.name,
        "image": args.image,
        "gpu": {"id": args.gpu, "count": 1},
        "cloud": args.cloud,
        "disk": args.disk_gb,
        # the volume: everything the scripts install lives here and survives a stop
        "mounts": {"persistent": {"size": args.volume_gb, "path": "/workspace"}},
        # only sshd is published; the engine's own port never is
        "ports": ["22/tcp"],
        "startSsh": True,
        "env": {"PUBLIC_KEY": public_key},
    }
    if args.datacenter:
        body["dataCenterIds"] = args.datacenter
    say(f"About to create a RunPod pod: {args.gpu}, {args.cloud} cloud, {args.volume_gb} GB volume + {args.disk_gb} GB disk, image {args.image}.")
    say("It is billed by the hour while it runs, and for its volume while it is stopped. The price is shown once RunPod has placed it.")
    if not args.yes and input("Create it? [y/N] ").strip().lower() not in ("y", "yes", "s", "sim"):
        die("nothing was created", 0)
    try:
        pod = api("POST", "/pods", body)
    except ApiError as exc:
        die(f"RunPod did not create the pod: {exc.detail}\nIf it has no {args.gpu} free, try another --gpu, --cloud COMMUNITY, or later.")
    lib.forget_host_keys()
    lib.save_state({"podId": pod["id"], "name": args.name})
    say(f"pod {pod['id']} created ({lib.money(pod)})")
    return pod


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy ACE-Step 1.5 to a RunPod pod (engine only; the studio stays on this computer).")
    parser.add_argument("--ace", choices=["latest", "pinned"], default="latest", help="latest: ACE-Step's newest commit, falling back to the tested one (default). pinned: the tested one")
    parser.add_argument("--gpu", default="NVIDIA GeForce RTX 4090", help='RunPod GPU type id (default: "NVIDIA GeForce RTX 4090"; e.g. "NVIDIA GeForce RTX 3090", "NVIDIA RTX A5000", "NVIDIA L4")')
    parser.add_argument("--cloud", choices=["SECURE", "COMMUNITY"], default="SECURE")
    parser.add_argument("--datacenter", action="append", help="preferred data center id; may be repeated")
    parser.add_argument("--volume-gb", type=int, default=60, help="volume for the engine, its environment and models (default 60)")
    parser.add_argument("--disk-gb", type=int, default=20, help="container disk, wiped on every stop (default 20)")
    parser.add_argument("--image", default="runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404", help="an official RunPod image (they start sshd from PUBLIC_KEY)")
    parser.add_argument("--name", default="nix-studio-ace")
    parser.add_argument("--idle-minutes", type=int, default=20, help="the pod stops itself after this long without a connection; 0 = never (default 20)")
    parser.add_argument("--keep-running", action="store_true", help="leave the pod running when the install is done")
    parser.add_argument("--yes", action="store_true", help="do not ask before creating the pod")
    args = parser.parse_args()
    if args.idle_minutes < 0:
        die("--idle-minutes cannot be negative")

    patch = Path(os.environ.get("NIX_ACE_PATCH") or lib.REPO / "external" / "patches" / "ace-step.patch")
    if not patch.is_file():
        die(f"the studio's ACE-Step patch is missing: {patch}")
    pinned = lib.pinned_ace_ref()

    pod = lib.our_pod(required=False)
    if pod is None:
        pod = create_pod(args)
    else:
        say(f"using the existing pod {pod['id']} ({pod.get('name')}, {lib.money(pod)}); the --gpu/--volume options apply to new pods only")
    pod = lib.ensure_running(pod)

    say("copying the install scripts and the patch to the pod")
    lib.upload_remote_scripts(pod, {"ace-step.patch": patch})
    lib.run_ssh(pod, f"mkdir -p {lib.REMOTE_HOME}/run && echo {args.idle_minutes} > {lib.REMOTE_HOME}/run/idle-minutes")

    say(f"installing ACE-Step ({args.ace}; tested commit {pinned}). The first install downloads PyTorch and the models: 15 to 40 minutes.")
    done = lib.run_ssh(pod, lib.remote("ace_bootstrap.sh", f"{lib.REMOTE_SCRIPTS}/ace-step.patch", pinned, args.ace), check=False)
    if done.returncode != 0:
        say("")
        say(f"The install failed. The pod is still RUNNING and charging ({lib.money(pod)}).")
        say("Fix and run this script again, or stop it:  python brand/scripts/runpod-models.py pod stop")
        raise SystemExit(done.returncode)
    version = lib.run_ssh(pod, f"cat {lib.REMOTE_HOME}/ace-version", capture=True, check=False).stdout.decode("utf-8", "replace").split("\n")
    say(f"installed: ACE-Step {version[0][:12]} [{version[1] if len(version) > 1 else '?'}]")

    # The studio starts "ACE-Step" by running `uv run acestep-api` in ACE_STEP_DIR and counts it as installed when
    # that folder has a .venv: prepare the stand-in project that opens the tunnel instead.
    if shutil.which("uv"):
        if subprocess.run(["uv", "sync", "-q"], cwd=SHIM).returncode != 0:
            say(f"warning: `uv sync` failed in {SHIM}; run it by hand before starting the studio")
    else:
        say(f"warning: `uv` is not installed here; the studio needs it. Then run `uv sync` in {SHIM}")

    if args.keep_running:
        # the idle guard normally starts with the engine; start it now so a pod left on is not left on for ever
        lib.run_ssh(pod, lib.remote("idle_guard.sh", "ensure"), check=False)
        say(f"the pod stays RUNNING and charging ({lib.money(pod)}); it stops itself after {args.idle_minutes} min without a connection"
            if args.idle_minutes else f"the pod stays RUNNING and charging ({lib.money(pod)}) until you stop it")
    else:
        lib.pod_action(pod["id"], "stop")
        say("pod stopped: only its volume is billed now")

    say("")
    say("To use it from the studio, put this line in backend/.env and start the studio as usual:")
    say(f"  ACE_STEP_DIR={SHIM}")
    say("Starting ACE-Step in the studio then starts the pod and the engine; stopping it stops the engine.")
    say("By hand:  python brand/scripts/runpod-models.py --help")


if __name__ == "__main__":
    try:
        main()
    except ApiError as exc:
        die(str(exc))
    except KeyboardInterrupt:
        die("interrupted; check the pod with: python brand/scripts/runpod-models.py pod status", 130)
