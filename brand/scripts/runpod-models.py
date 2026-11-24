#!/usr/bin/env python3
"""Starts and stops the RunPod pod and the engine on it.

    python brand/scripts/runpod-models.py pod status            what is running, and what it costs
    python brand/scripts/runpod-models.py pod start | stop      the machine itself: stop is what ends the GPU bill
    python brand/scripts/runpod-models.py pod terminate         delete the pod and its volume for good

    python brand/scripts/runpod-models.py model start ace       start ACE-Step on the pod (starts the pod if needed)
    python brand/scripts/runpod-models.py model stop ace        stop it and free the GPU memory; the pod keeps running
    python brand/scripts/runpod-models.py model status ace
    python brand/scripts/runpod-models.py model logs ace

    python brand/scripts/runpod-models.py connect ace           start the engine and hold an SSH tunnel to it on
                                                                127.0.0.1:8001 until Ctrl+C, then stop the engine

`connect` is what the studio itself runs when you start ACE-Step in it (see brand/scripts/README.md), so in
everyday use the studio's own start and stop buttons are enough. The pod stops itself after 20 minutes without
a connection (runpod-deploy.py --idle-minutes).
"""
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "runpod"))
import lib  # noqa: E402
from lib import ApiError, die, say  # noqa: E402


def engine_script(engine: str) -> str:
    if engine == "yue":
        die(lib.YUE_NOTE)
    return lib.ENGINES[engine]


def engine_status(pod: dict, engine: str) -> str:
    """One of: running, starting, stopped (as the pod-side script reports it)."""
    done = lib.run_ssh(pod, lib.remote(engine_script(engine), "status"), capture=True, check=False)
    line = done.stdout.decode("utf-8", "replace").strip()
    return line.split()[1] if done.returncode == 0 and len(line.split()) > 1 else "unknown"


def wait_engine(pod: dict, engine: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = engine_status(pod, engine)
        if status == "running":
            return
        if status == "stopped":
            lib.run_ssh(pod, lib.remote(engine_script(engine), "logs", "40"), check=False)
            die("the engine exited while starting (its last log lines are above)")
        time.sleep(5)
    die(f"the engine did not answer in {int(timeout)} s; see: runpod-models.py model logs {engine}")


# ---------------------------------------------------------------- pod

def cmd_pod(args: argparse.Namespace) -> None:
    pod = lib.our_pod()
    if args.action == "status":
        say(f"pod {pod['id']} ({pod.get('name')}): {pod.get('status')}, {lib.money(pod)} while running")
        if pod.get("status") == "RUNNING" and (pod.get("ssh") or {}).get("direct"):
            for engine in lib.ENGINES:
                done = lib.run_ssh(pod, lib.remote(lib.ENGINES[engine], "status"), capture=True, check=False)
                say("  " + (done.stdout.decode("utf-8", "replace").strip() or f"{engine}: no answer over SSH"))
        elif pod.get("status") == "EXITED":
            say("  stopped: only its volume is billed")
    elif args.action == "start":
        lib.ensure_running(pod)
        say(f"pod is running ({lib.money(pod)}). Nothing stops it by itself until an engine has been started on it.")
    elif args.action == "stop":
        if pod.get("status") == "EXITED":
            say("the pod is already stopped")
        else:
            lib.pod_action(pod["id"], "stop")
            say("pod stopped: the GPU is released, its volume (the install and the models) is kept and billed")
    elif args.action == "terminate":
        say(f"This deletes pod {pod['id']} and its volume: ACE-Step and the downloaded models on it are gone for good.")
        if not args.yes and input("Type the pod id to confirm: ").strip() != pod["id"]:
            die("nothing was deleted", 0)
        lib.pod_action(pod["id"], "terminate")
        lib.STATE_FILE.unlink(missing_ok=True)
        lib.forget_host_keys()
        say("pod terminated")


# ---------------------------------------------------------------- model

def cmd_model(args: argparse.Namespace) -> None:
    script = engine_script(args.engine)
    pod = lib.our_pod()
    if args.action == "start":
        pod = lib.ensure_running(pod)
        lib.upload_remote_scripts(pod)
        lib.run_ssh(pod, lib.remote(script, "start", str(args.port), args.dit, args.lm))
        say("waiting for the engine to load its models")
        wait_engine(pod, args.engine, args.timeout)
        say(f"{args.engine} is running on the pod. The pod charges {lib.money(pod)} until it is stopped; it stops itself when nobody connects for a while.")
        return
    if pod.get("status") != "RUNNING":
        say(f"the pod is {pod.get('status')}: no engine is running on it")
        return
    if args.action == "stop":
        lib.run_ssh(pod, lib.remote(script, "stop"))
        say(f"The pod itself is still running and charging ({lib.money(pod)}). To end that: runpod-models.py pod stop")
    elif args.action == "status":
        lib.run_ssh(pod, lib.remote(script, "status"))
    elif args.action == "logs":
        lib.run_ssh(pod, lib.remote(script, "logs", str(args.lines)))


# ---------------------------------------------------------------- connect

def cmd_connect(args: argparse.Namespace) -> None:
    """Engine up, tunnel open, both down again when this process is told to end. Runs in the foreground."""
    script = engine_script(args.engine)
    pod = lib.ensure_running(lib.our_pod())
    lib.upload_remote_scripts(pod)
    lib.run_ssh(pod, lib.remote(script, "start", str(args.port), args.dit, args.lm))

    # The remote loop is the heartbeat the pod's idle guard watches: it runs exactly as long as this connection.
    heartbeat = f"while :; do touch {lib.REMOTE_HOME}/run/heartbeat; sleep 30; done"
    forward = f"127.0.0.1:{args.port}:127.0.0.1:{args.port}"
    tunnel = subprocess.Popen([*lib.ssh_args(pod, "-o", "ExitOnForwardFailure=yes", "-L", forward), heartbeat], stdin=subprocess.DEVNULL)
    say(f"tunnel open: http://127.0.0.1:{args.port} is ACE-Step on the pod (it answers once the models are loaded)")

    stopping = False

    def end(*_: object) -> None:
        nonlocal stopping
        stopping = True
        tunnel.terminate()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), end)

    code = tunnel.wait()
    # Leave nothing loaded on the GPU. If this process is killed outright (the studio does that on Windows), the
    # engine stays up and the pod's idle guard ends it, and the pod, a little later.
    lib.run_ssh(pod, lib.remote(script, "stop"), check=False)
    if args.stop_pod:
        lib.pod_action(pod["id"], "stop")
        say("pod stopped")
    if not stopping and code != 0:
        die(f"the tunnel closed by itself (ssh exit {code}): is port {args.port} already in use on this computer?", code)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start and stop the RunPod pod and the engine on it.")
    commands = parser.add_subparsers(dest="command", required=True)

    pod = commands.add_parser("pod", help="the machine: status, start, stop, terminate")
    pod.add_argument("action", choices=["status", "start", "stop", "terminate"])
    pod.add_argument("--yes", action="store_true", help="terminate without asking")
    pod.set_defaults(run=cmd_pod)

    def engine_options(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("engine", choices=["ace", "yue"])
        sub.add_argument("--port", type=int, default=8001, help="the studio's ACE_STEP_API_PORT (default 8001)")
        sub.add_argument("--dit", default="acestep-v15-turbo", help="ACE-Step DiT model")
        sub.add_argument("--lm", default="acestep-5Hz-lm-1.7B", help="ACE-Step language model")

    model = commands.add_parser("model", help="the engine on the pod: start, stop, status, logs")
    model.add_argument("action", choices=["start", "stop", "status", "logs"])
    engine_options(model)
    model.add_argument("--timeout", type=int, default=900, help="seconds to wait for the engine to answer (default 900)")
    model.add_argument("--lines", type=int, default=60)
    model.set_defaults(run=cmd_model)

    connect = commands.add_parser("connect", help="start the engine and hold a tunnel to it until interrupted")
    engine_options(connect)
    connect.add_argument("--stop-pod", action="store_true", help="also stop the pod when the tunnel closes")
    connect.set_defaults(run=cmd_connect)

    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    try:
        main()
    except ApiError as exc:
        die(str(exc))
    except KeyboardInterrupt:
        raise SystemExit(130)
