# ACE-Step on RunPod, for a studio on your own computer

The studio (NiX Studio on `localhost`) stays on your machine; only the engine runs on a rented GPU. Two scripts:

| Script | What it does |
|---|---|
| `runpod-deploy.py` | Creates the pod and installs ACE-Step 1.5 on it. Run it again to update. |
| `runpod-models.py` | Starts and stops the pod and the engine, and holds the tunnel between the two. |

They need Python 3.10+, the `ssh` command and an SSH key (`ssh-keygen -t ed25519` makes one). No other install.

## Set up, once

```sh
export RUNPOD_API_KEY=...                    # RunPod console → Settings → API Keys (pods: read and write)
python brand/scripts/runpod-deploy.py        # shows what it will create and asks before it does
```

It creates a pod (RTX 4090, 60 GB volume, by default), installs ACE-Step, checks that its API starts, and stops
the pod again. The first run takes 15 to 40 minutes: PyTorch and the models are several GB. Then add the line it
prints to `backend/.env`:

```
ACE_STEP_DIR=<repo>/brand/scripts/runpod/shim/ace-step
```

## Use

Start the studio as usual and start ACE-Step in it. Behind that button the pod is started, the engine is
started, and an SSH tunnel makes it answer on `127.0.0.1:8001`, exactly where the studio expects a local
ACE-Step. A cold start takes a few minutes (the pod boots, the models load). Stopping ACE-Step in the studio
stops the engine on the pod.

By hand, when you want to:

```sh
python brand/scripts/runpod-models.py pod status         # what is running and what it costs
python brand/scripts/runpod-models.py pod stop           # ends the GPU bill
python brand/scripts/runpod-models.py model start ace    # and stop, status, logs
python brand/scripts/runpod-models.py pod terminate      # deletes the pod and its volume
```

## What it costs, and what stops it

- A running pod is billed by the hour **whether or not the engine is running**. Stopping ACE-Step in the studio
  frees the GPU memory; it does not stop the bill.
- The pod stops itself after 20 minutes without a connection from the studio (`--idle-minutes`, `0` to turn it
  off). That covers a closed laptop or a crashed studio.
- A stopped pod is billed only for its volume. `pod terminate` ends that too, and deletes the install.
- Set `NIX_RUNPOD_STOP_POD=1` before starting the studio to stop the pod every time ACE-Step is stopped in it.
  Cheaper, but every start is then a cold start.

## "Always the latest version"

Every run of `runpod-deploy.py` tries ACE-Step's newest commit. The studio talks to ACE-Step through routes that
its own patch (`external/patches/ace-step.patch`) adds, so a commit is kept only if the patch applies to it, its
environment installs and its API server answers. Otherwise the commit the studio was tested with is installed
(the one upstream pins in `setup_linux.sh`), and the script says why. `--ace pinned` skips the attempt.

The check is that the API starts, not that a song comes out right: a new ACE-Step commit can pass it and still
behave differently. If generation misbehaves after an update, run `runpod-deploy.py --ace pinned`.

## Security

Nothing on the pod is public except its SSH port. The engine listens on `127.0.0.1` inside the pod and is
reached only through the tunnel, with your SSH key. The pod's host key is learned on the first connection after
each start (a stopped pod comes back with a new key and address) and checked for the rest of that run.

## What does not work remotely

The studio still looks at this computer for some things, so with a remote ACE-Step:

- The Models page shows ACE-Step's models as not installed, and its download and delete buttons act on this
  computer, not the pod. The pod downloads the models it needs by itself.
- Features the studio enables only when it finds the base or XL model files locally (the AI arranger's base
  model, XL) stay off.
- LoRA training and datasets use local folders and are not connected to the pod.
- **YuE2** is not supported yet: the studio decides that YuE2 is installed by finding its server binary and
  model files on this computer, so a remote YuE2 needs a change in the studio itself, not just a script.

## How it was tested

Against stand-ins for RunPod's API, for `ssh` and for ACE-Step: creating and reusing the pod, the fallback to
the tested commit (patch does not apply; API does not start), start/stop of pod and engine, the tunnel, the idle
guard, and the real studio backend starting and stopping "ACE-Step" through the stand-in project. **Not yet run
against a real RunPod account or a real GPU**: expect to adjust details (image, timings) on the first real run.

The scripts use RunPod's REST API v2 (`api.runpod.io/v2`); v1 was retired in November 2026.
