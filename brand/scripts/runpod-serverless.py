#!/usr/bin/env python3
"""ACE-Step on RunPod Serverless, for a studio that runs on this computer: a GPU only while it is used.

    export RUNPOD_API_KEY=...
    python brand/scripts/runpod-serverless.py deploy      # global volume + endpoint, then downloads the models once
    python brand/scripts/runpod-serverless.py status      # the endpoint and its workers
    python brand/scripts/runpod-serverless.py warm        # starts a worker and waits until ACE-Step answers
    python brand/scripts/runpod-serverless.py logs        # the last lines of the newest worker
    python brand/scripts/runpod-serverless.py destroy     # deletes the endpoint (--volume: and the models)

The endpoint is a load-balancing one: its URL forwards HTTP straight to ACE-Step's own API inside the worker,
behind the RunPod API key. Workers start when a request comes, stop after --idle-seconds without one, and are
billed only while they run. The models live on a global volume, so a worker can start in any data center that
has a GPU free. The worker image is built by .github/workflows/nix-ace-image.yml. See brand/scripts/README.md.

`connect` is what the studio runs (through brand/scripts/runpod/shim/ace-step): it warms the endpoint up and
serves it on 127.0.0.1:<port>, where the studio expects a local ACE-Step.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "runpod"))
import lib  # noqa: E402
import proxy  # noqa: E402
from lib import ApiError, api, die, say  # noqa: E402

STATE = lib.HERE / ".serverless.json"
NAME = "nix-studio-ace"
VOLUME_NAME = "nix-studio-ace-models"
DEFAULT_IMAGE = "ghcr.io/tgonzales/nix-ace-step:latest"
# Serverless GPU pools with 24 GB or more (RTX 3090/A5000, RTX 4090, A40/A6000, L40/L40S/6000 Ada). Any one is
# enough for the turbo DiT with the 1.7B LM; listing several lets RunPod use whichever has a GPU free.
DEFAULT_POOLS = ["AMPERE_24", "ADA_24", "AMPERE_48", "ADA_48_PRO"]
ACE_PORT, HEALTH_PORT = 8000, 8080
DEFAULT_DIT, DEFAULT_LM = "acestep-v15-turbo", "acestep-5Hz-lm-1.7B"


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def find(path: str, key: str, name: str) -> dict[str, Any] | None:
    cursor = None
    while True:
        page = api("GET", f"{path}?limit=100" + (f"&cursor={cursor}" if cursor else ""))
        for item in page.get(key, []):
            if item.get("name") == name:
                return item
        cursor = (page.get("pagination") or {}).get("nextCursor")
        if not cursor:
            return None


def endpoint(required: bool = True) -> dict[str, Any] | None:
    endpoint_id = os.environ.get("NIX_RUNPOD_ENDPOINT_ID") or load_state().get("endpointId")
    found = None
    if endpoint_id:
        try:
            found = api("GET", f"/serverless/{endpoint_id}")
        except ApiError as exc:
            if exc.status != 404:
                raise
    if found is None and required:
        die("no endpoint yet: run  python brand/scripts/runpod-serverless.py deploy")
    return found


def base_url(ep: dict[str, Any]) -> str:
    url = ((ep.get("requestUrls") or {}).get("base") or "").rstrip("/")
    if not url:
        die(f"RunPod gave the endpoint {ep.get('id')} no load-balancing URL; is it a LOAD_BALANCER endpoint?")
    return url


def pools(wanted: list[str]) -> list[str]:
    """The wanted pools that RunPod knows, with what each costs, so a typo or a retired pool is caught here."""
    catalog = api("GET", "/catalog/gpus?include=AVAILABILITY&product=SERVERLESS")
    gpus = catalog if isinstance(catalog, list) else catalog.get("gpus") or catalog.get("items") or []
    by_pool: dict[str, list[dict[str, Any]]] = {}
    for gpu in gpus:
        if gpu.get("pool"):
            by_pool.setdefault(gpu["pool"], []).append(gpu)
    known = [p for p in wanted if p in by_pool]
    for p in wanted:
        if p not in by_pool:
            say(f"warning: RunPod has no serverless pool {p}; left out")
    if not known:
        die(f"none of the pools {wanted} exists; known: {', '.join(sorted(by_pool))}")
    for p in known:
        cards = ", ".join(f"{g.get('name') or g['id']} US$ {((g.get('price') or {}).get('serverless') or 0):.2f}/h"
                          for g in by_pool[p])
        say(f"  {p}: {cards}")
    return known


def env_for(dit: str, lm: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    return {**(extra or {}), "PORT": str(ACE_PORT), "PORT_HEALTH": str(HEALTH_PORT), "HEALTH_CHECK_PATH": "/ping",
            "ACE_DIT_MODEL": dit, "ACE_LM_MODEL": lm}


# ---------------------------------------------------------------- commands

def cmd_deploy(args: argparse.Namespace) -> None:
    volume = find("/global-volumes", "globalVolumes", VOLUME_NAME)
    if volume is None:
        say(f"creating the global volume {VOLUME_NAME} for the models (billed by what it stores, about 10 GB)")
        volume = api("POST", "/global-volumes", {"name": VOLUME_NAME})
    else:
        say(f"using the global volume {volume['id']} ({VOLUME_NAME})")

    say("GPU pools (serverless prices, billed by the second while a worker runs):")
    body: dict[str, Any] = {
        "image": args.image,
        "gpu": {"pools": pools(args.pool or DEFAULT_POOLS), "count": 1},
        # one worker: ACE-Step keeps its tasks and its output files in the worker, so every request of a session
        # must reach the same one
        "workers": {"min": 0, "max": 1, "idleTimeout": args.idle_seconds, "initTimeout": 1800},
        "scaling": {"type": "REQUEST_COUNT", "requestCount": 100},
        "env": env_for(args.dit, args.lm),
        "ports": [f"{ACE_PORT}/http", f"{HEALTH_PORT}/http"],
        "disk": 30,
        "mounts": {"global": [{"volumeId": volume["id"]}]},
        "flashboot": "FLASHBOOT",
        "timeout": 330000,
    }
    ep = endpoint(required=False) or find("/serverless", "endpoints", NAME)
    if ep is None:
        if not args.yes and input("Create the endpoint? It costs nothing until a request starts a worker. [y/N] ").strip().lower() not in ("y", "yes", "s", "sim"):
            die("nothing was created", 0)
        ep = api("POST", "/serverless", {"name": NAME, "type": "LOAD_BALANCER", **body})
        say(f"endpoint {ep['id']} created")
    else:
        ep = api("PATCH", f"/serverless/{ep['id']}", body)
        say(f"endpoint {ep['id']} updated (workers already running finish on the old settings)")
    save_state({"endpointId": ep["id"], "volumeId": volume["id"], "url": base_url(ep)})
    say(f"  URL: {base_url(ep)}")

    if args.no_warm:
        say("not warmed up: the first start downloads the models, which can outlast the studio's 10-minute wait")
    else:
        say("starting a worker once: the first start downloads the models to the volume (several minutes)")
        warm_up(ep, deadline_s=args.first_start_minutes * 60)
        say(f"ready. The worker stops by itself after {args.idle_seconds} s without a request.")
    say("")
    say("To use it from the studio, put these lines in backend/.env and start the studio as usual:")
    say(f"  ACE_STEP_DIR={lib.HERE / 'shim' / 'ace-step'}")
    say("  NIX_RUNPOD_MODE=serverless")


def warm_up(ep: dict[str, Any], deadline_s: float) -> bytes:
    upstream = proxy.Upstream(base_url(ep), os.environ.get("RUNPOD_API_KEY", ""))
    started = time.monotonic()
    try:
        health = proxy.warm(upstream, deadline_s)
    except TimeoutError as exc:
        die(f"{exc}\nSee what the worker says:  python brand/scripts/runpod-serverless.py logs")
    say(f"ACE-Step answers ({time.monotonic() - started:.0f} s)")
    return health


def cmd_status(_args: argparse.Namespace) -> None:
    ep = endpoint()
    workers = ep.get("workers") or {}
    say(f"endpoint {ep['id']} ({ep.get('name')}): {ep.get('image')}")
    say(f"  URL {base_url(ep)}  pools {', '.join((ep.get('gpu') or {}).get('pools') or [])}")
    say(f"  workers {workers.get('min')}..{workers.get('max')}, stop after {workers.get('idleTimeout')} s idle; "
        f"models {(ep.get('env') or {}).get('ACE_DIT_MODEL')} + {(ep.get('env') or {}).get('ACE_LM_MODEL')}")
    running = api("GET", f"/serverless/{ep['id']}/workers")
    items = running.get("workers") or []
    if not items:
        say("  no worker running: nothing is billed but the volume")
    for w in items:
        say(f"  worker {w.get('id')}: {w.get('status')} on {w.get('gpuTypeId') or '?'} in {w.get('dataCenterId') or '?'}, "
            f"up {w.get('uptimeSeconds') or 0} s{' (old settings)' if w.get('isStale') else ''}")


def cmd_warm(_args: argparse.Namespace) -> None:
    warm_up(endpoint(), deadline_s=proxy.COLD_START_SECONDS)


def cmd_logs(args: argparse.Namespace) -> None:
    """RunPod streams a worker's log as Server-Sent Events: print the last lines, and the new ones with --follow."""
    ep = endpoint()
    items = api("GET", f"/serverless/{ep['id']}/workers").get("workers") or []
    if not items:
        die("no worker is running, so there is no log to read", 0)
    worker = max(items, key=lambda w: w.get("startedAt") or "")
    request = urllib.request.Request(
        f"{lib.API}/serverless/{ep['id']}/workers/{worker['id']}/logs?tail={args.lines}",
        headers={"Authorization": f"Bearer {os.environ.get('RUNPOD_API_KEY', '')}", "Accept": "text/event-stream"})
    say(f"worker {worker['id']} ({worker.get('status')} on {worker.get('gpuTypeId') or '?'}):")
    try:
        with urllib.request.urlopen(request, timeout=None if args.follow else 5) as stream:
            for raw in stream:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("data:"):
                    try:
                        event = json.loads(line[5:])
                        print(f"{event.get('ts', '')[11:19]} {event.get('line', '')}")
                    except ValueError:
                        print(line[5:].strip())
    except (TimeoutError, socket.timeout):
        pass  # no new line for a few seconds: without --follow, that is the end of the backlog


def cmd_destroy(args: argparse.Namespace) -> None:
    ep = endpoint(required=False)
    state = load_state()
    if ep is not None:
        if not args.yes and input(f"Delete the endpoint {ep['id']}? [y/N] ").strip().lower() not in ("y", "yes", "s", "sim"):
            die("nothing was deleted", 0)
        api("DELETE", f"/serverless/{ep['id']}")  # also ends any worker still running
        say(f"endpoint {ep['id']} deleted")
    if args.volume:
        volume_id = state.get("volumeId") or (find("/global-volumes", "globalVolumes", VOLUME_NAME) or {}).get("id")
        if volume_id:
            api("DELETE", f"/global-volumes/{volume_id}")
            say(f"global volume {volume_id} deleted: the models are gone, the next deploy downloads them again")
    STATE.unlink(missing_ok=True)


def cmd_connect(args: argparse.Namespace) -> None:
    """What the studio runs: the endpoint on 127.0.0.1:<port> until the studio stops it."""
    ep = endpoint()
    env = ep.get("env") or {}
    if env.get("ACE_DIT_MODEL") != args.dit or env.get("ACE_LM_MODEL") != args.lm:
        say(f"switching the endpoint to {args.dit} + {args.lm} (a new worker starts with them)")
        ep = api("PATCH", f"/serverless/{ep['id']}", {"env": env_for(args.dit, args.lm, env)})
    health = warm_up(ep, deadline_s=proxy.COLD_START_SECONDS)
    stop = threading.Event()
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    upstream = proxy.Upstream(base_url(ep), os.environ.get("RUNPOD_API_KEY", ""))
    proxy.serve(upstream, args.port, health, stop)
    say(f"disconnected; the worker stops by itself after {(ep.get('workers') or {}).get('idleTimeout')} s idle")


def main() -> None:
    parser = argparse.ArgumentParser(description="ACE-Step on RunPod Serverless for a studio on this computer.")
    commands = parser.add_subparsers(dest="command", required=True)

    deploy = commands.add_parser("deploy", help="create or update the global volume and the endpoint")
    deploy.add_argument("--image", default=os.environ.get("NIX_ACE_IMAGE", DEFAULT_IMAGE))
    deploy.add_argument("--pool", action="append", help=f"serverless GPU pool; may be repeated (default: {' '.join(DEFAULT_POOLS)})")
    deploy.add_argument("--idle-seconds", type=int, default=300, help="a worker stops after this long without a request (default 300)")
    deploy.add_argument("--dit", default=DEFAULT_DIT)
    deploy.add_argument("--lm", default=DEFAULT_LM)
    deploy.add_argument("--first-start-minutes", type=int, default=30, help="how long the first start may take (models download)")
    deploy.add_argument("--no-warm", action="store_true", help="do not start a worker after deploying")
    deploy.add_argument("--yes", action="store_true", help="do not ask before creating the endpoint")
    deploy.set_defaults(run=cmd_deploy)

    commands.add_parser("status", help="the endpoint and its workers").set_defaults(run=cmd_status)
    commands.add_parser("warm", help="start a worker and wait until ACE-Step answers").set_defaults(run=cmd_warm)
    logs = commands.add_parser("logs", help="the newest worker's log")
    logs.add_argument("--lines", type=int, default=100)
    logs.add_argument("--follow", action="store_true", help="keep printing new lines until Ctrl+C")
    logs.set_defaults(run=cmd_logs)
    destroy = commands.add_parser("destroy", help="delete the endpoint")
    destroy.add_argument("--volume", action="store_true", help="also delete the global volume with the models")
    destroy.add_argument("--yes", action="store_true")
    destroy.set_defaults(run=cmd_destroy)

    connect = commands.add_parser("connect", help="serve the endpoint on 127.0.0.1 for the studio")
    connect.add_argument("--port", type=int, default=8001)
    connect.add_argument("--dit", default=DEFAULT_DIT)
    connect.add_argument("--lm", default=DEFAULT_LM)
    connect.set_defaults(run=cmd_connect)

    args = parser.parse_args()
    if args.command == "deploy" and args.idle_seconds < 1:
        die("--idle-seconds must be at least 1")
    args.run(args)


if __name__ == "__main__":
    try:
        main()
    except ApiError as exc:
        die(str(exc))
    except KeyboardInterrupt:
        die("interrupted", 130)
