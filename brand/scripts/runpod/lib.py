"""What runpod-deploy.py and runpod-models.py share: the RunPod API (REST v2), the pod's SSH connection and
the small state file that remembers which pod is ours. Standard library only, so the scripts run with any
Python 3.10+ on Windows, macOS and Linux; the one outside tool they need is the `ssh` command.
"""
from __future__ import annotations

import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NoReturn

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
REMOTE_SRC = HERE / "remote"
STATE_FILE = HERE / ".state.json"
KNOWN_HOSTS = HERE / ".known_hosts"

API = os.environ.get("RUNPOD_API_URL", "https://api.runpod.io/v2").rstrip("/")
REMOTE_HOME = "/workspace/nix"            # on the pod's volume: survives a stop
REMOTE_SCRIPTS = f"{REMOTE_HOME}/scripts"
ENGINES = {"ace": "ace_engine.sh"}
YUE_NOTE = (
    "YuE2 is not available on the pod yet: the studio decides whether YuE2 is installed by looking for its\n"
    "server binary and model files on this computer, so a remote YuE2 needs a change in the studio first."
)


def say(text: str) -> None:
    print(text, flush=True)


def die(text: str, code: int = 1) -> NoReturn:
    print(f"error: {text}", file=sys.stderr, flush=True)
    raise SystemExit(code)


# ---------------------------------------------------------------- state

def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def forget_host_keys() -> None:
    """The pod's sshd makes new host keys on every start (its container disk is wiped) and its address changes
    too, so the keys remembered from the last start are dropped and the next connection learns the new ones."""
    KNOWN_HOSTS.unlink(missing_ok=True)


# ---------------------------------------------------------------- RunPod API

class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"RunPod API answered {status}: {detail}")
        self.status = status
        self.detail = detail


def api(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    key = os.environ.get("RUNPOD_API_KEY", "").strip()
    if not key:
        die("set RUNPOD_API_KEY (RunPod console → Settings → API Keys; it needs the pods read and write scopes)")
    request = urllib.request.Request(
        f"{API}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json", **({"Content-Type": "application/json"} if body is not None else {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            problem = json.loads(raw)
            detail = problem.get("detail") or problem.get("title") or raw
            if problem.get("errors"):
                detail += " (" + "; ".join(map(str, problem["errors"])) + ")"
        except ValueError:
            detail = raw[:300] or exc.reason
        raise ApiError(exc.code, detail) from None
    except urllib.error.URLError as exc:
        die(f"could not reach RunPod ({API}): {exc.reason}")


def get_pod(pod_id: str) -> dict[str, Any] | None:
    try:
        return api("GET", f"/pods/{pod_id}")
    except ApiError as exc:
        if exc.status == 404:
            return None
        raise


def our_pod(required: bool = True) -> dict[str, Any] | None:
    """The pod runpod-deploy.py created, as RunPod reports it now."""
    pod_id = os.environ.get("NIX_RUNPOD_POD_ID") or load_state().get("podId")
    pod = get_pod(pod_id) if pod_id else None
    if pod and pod.get("status") == "TERMINATED":
        pod = None
    if pod is None and required:
        die("no pod yet (or it was terminated): run brand/scripts/runpod-deploy.py first")
    return pod


def pod_action(pod_id: str, action: str) -> dict[str, Any] | None:
    try:
        return api("POST", f"/pods/{pod_id}/action", {"action": action})
    except ApiError as exc:
        if exc.status == 409:
            die(f"RunPod refused to {action} the pod in its current state: {exc.detail}")
        raise


def wait_running(pod_id: str, timeout: float = 900) -> dict[str, Any]:
    """Until the pod is RUNNING and RunPod has published the port of its sshd."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        pod = get_pod(pod_id)
        if pod is None:
            die("the pod disappeared while it was starting")
        status = pod.get("status", "?")
        if status != last:
            say(f"  pod is {status}")
            last = status
        if status == "ERROR":
            die("the pod's container failed to start: see its logs in the RunPod console")
        if status == "RUNNING" and (pod.get("ssh") or {}).get("direct"):
            return pod
        time.sleep(5)
    die(f"the pod did not become reachable in {int(timeout)} s (status {last})")


def ensure_running(pod: dict[str, Any]) -> dict[str, Any]:
    """Starts a stopped pod and waits until SSH answers. A pod that is already up is returned as it is."""
    state = load_state()
    if pod.get("status") in ("EXITED", "ERROR"):
        say("starting the pod (billing for the GPU starts now)")
        pod_action(pod["id"], "start")
    pod = wait_running(pod["id"])
    if state.get("startedAt") != pod.get("startedAt"):
        forget_host_keys()
        save_state({**state, "podId": pod["id"], "startedAt": pod.get("startedAt")})
    wait_ssh(pod)
    return pod


# ---------------------------------------------------------------- SSH

def ssh_key() -> Path:
    """The private key to connect with. Its .pub half is what runpod-deploy.py gives the pod."""
    explicit = os.environ.get("NIX_RUNPOD_SSH_KEY")
    candidates = [Path(explicit).expanduser()] if explicit else [Path.home() / ".ssh" / name for name in ("id_ed25519", "id_ecdsa", "id_rsa")]
    for key in candidates:
        if key.is_file() and Path(f"{key}.pub").is_file():
            return key
    die("no SSH key found. Make one with:  ssh-keygen -t ed25519   (or point NIX_RUNPOD_SSH_KEY at a private key that has a .pub next to it)")


def ssh_args(pod: dict[str, Any], *extra: str) -> list[str]:
    direct = (pod.get("ssh") or {}).get("direct")
    if not direct:
        die("the pod has no direct SSH address: it must be running and created with port 22/tcp (runpod-deploy.py does that)")
    return [
        "ssh", "-i", str(ssh_key()), "-p", str(direct["port"]),
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=4",
        # the first connection after a start learns the pod's host key; a different key later is refused
        "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}", "-o", "LogLevel=ERROR",
        *extra, f"{direct['username']}@{direct['host']}",
    ]


def run_ssh(pod: dict[str, Any], command: str, *, stdin: bytes | None = None, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    try:
        done = subprocess.run([*ssh_args(pod), command], input=stdin, capture_output=capture)
    except FileNotFoundError:
        die("the `ssh` command was not found. On Windows: Settings → Optional features → OpenSSH Client")
    if check and done.returncode != 0:
        detail = (done.stderr or b"").decode("utf-8", "replace").strip() if capture else ""
        die(f"the command on the pod failed (exit {done.returncode}){': ' + detail if detail else ''}", done.returncode or 1)
    return done


def wait_ssh(pod: dict[str, Any], timeout: float = 180) -> None:
    """sshd comes up a little after RunPod reports RUNNING."""
    deadline = time.monotonic() + timeout
    while True:
        done = run_ssh(pod, "true", capture=True, check=False)
        if done.returncode == 0:
            return
        if time.monotonic() > deadline:
            die("SSH to the pod does not answer: " + (done.stderr or b"").decode("utf-8", "replace").strip()[:300]
                + "\nIs the public half of your key registered? runpod-deploy.py passes it to new pods; an older pod has the key it was created with.")
        time.sleep(5)


def remote(script: str, *args: str) -> str:
    """The command line that runs one of the pod-side scripts."""
    return " ".join(["bash", f"{REMOTE_SCRIPTS}/{script}", *(shlex.quote(a) for a in args)])


def upload(pod: dict[str, Any], files: dict[str, Path | bytes], directory: str = REMOTE_SCRIPTS) -> None:
    """Copies files to a directory on the pod through the SSH connection itself (no scp or rsync needed)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, source in files.items():
            data = source if isinstance(source, bytes) else source.read_bytes()
            if name.endswith(".sh"):
                data = data.replace(b"\r\n", b"\n")  # a Windows checkout may have CRLF; bash on the pod must not
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
    target = shlex.quote(directory)
    run_ssh(pod, f"mkdir -p {target} && tar -xzf - -C {target}", stdin=buffer.getvalue())


def upload_remote_scripts(pod: dict[str, Any], extra: dict[str, Path | bytes] | None = None) -> None:
    files: dict[str, Path | bytes] = {p.name: p for p in sorted(REMOTE_SRC.glob("*.sh"))}
    upload(pod, {**files, **(extra or {})})


# ---------------------------------------------------------------- the studio's own pins

def pinned_ace_ref() -> str:
    """The ACE-Step commit this checkout of the studio was tested with: read from upstream's setup script, so it
    follows upstream whenever the branch is synced."""
    override = os.environ.get("NIX_ACE_PINNED_REF", "").strip()
    if override:
        return override
    text = (REPO / "setup_linux.sh").read_text(encoding="utf-8")
    match = re.search(r'ACE_STEP_REF="\$\{ACE_STEP_REF:-([0-9a-f]{7,40})\}"', text)
    if not match:
        die("could not find ACE_STEP_REF in setup_linux.sh: upstream changed how it pins ACE-Step; update pinned_ace_ref() in brand/scripts/runpod/lib.py")
    return match.group(1)


def money(pod: dict[str, Any]) -> str:
    cost = pod.get("cost")
    return f"US$ {cost:.2f}/h" if isinstance(cost, (int, float)) else "price unknown"
