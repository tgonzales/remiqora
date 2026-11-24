#!/usr/bin/env bash
# Runs inside `docker build`: installs ACE-Step 1.5 with the studio's patch into $ACE_DIR.
#
#   build_ace.sh latest|pinned PINNED_REF REPO_URL
#
# Same rule as remote/ace_bootstrap.sh on a pod, without the API smoke test (there is no GPU during a build):
# the newest commit is kept only when the patch applies to it and its environment installs.
set -euo pipefail
MODE="${1:?latest|pinned}"; PINNED="${2:?the tested ACE-Step commit}"; REPO="${3:?repository url}"
PATCH=/opt/nix/ace-step.patch
say() { printf '[nix] %s\n' "$*"; }

git clone -q "$REPO" "$ACE_DIR"
latest="$(git -C "$ACE_DIR" rev-parse HEAD)"

why=""
install() {
  local ref="$1"
  git -C "$ACE_DIR" reset -q --hard
  git -C "$ACE_DIR" clean -q -fdx -e .venv
  git -C "$ACE_DIR" -c advice.detachedHead=false checkout -q --force --detach "$ref" || { why="commit $ref not found"; return 1; }
  git -C "$ACE_DIR" apply --check --whitespace=nowarn "$PATCH" || { why="the studio's patch does not apply to it"; return 1; }
  git -C "$ACE_DIR" apply --whitespace=nowarn "$PATCH"
  say "installing the Python environment for ${ref:0:12}"
  (cd "$ACE_DIR" && uv sync) || { why="its Python environment did not install"; return 1; }
}

kind=""
if [[ "$MODE" == latest ]]; then
  say "ACE-Step's latest commit is ${latest:0:12}"
  if install "$latest"; then kind="latest"; else say "the latest commit cannot be used: $why; using the tested commit $PINNED"; fi
fi
if [[ -z "$kind" ]]; then
  install "$PINNED" || { say "error: the tested commit $PINNED failed too: $why"; exit 1; }
  kind="pinned"
fi

# The models live on a RunPod global volume, a network filesystem that refuses to set a file's times or mode
# (EPERM). ACE-Step copies files into the checkpoints with shutil.copy2, which then fails after the data was
# copied. A .pth file loaded at interpreter start makes copying metadata best-effort for that error only.
site="$("$ACE_DIR/.venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
cat > "$site/nix_volume_fix.py" <<'PY'
"""NiX: on a RunPod network volume, copying a file's times or mode fails with EPERM; the data is already copied."""
import errno
import shutil

def _tolerant(original):
    def wrapper(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except PermissionError as exc:
            if exc.errno != errno.EPERM:
                raise
    return wrapper

shutil.copystat = _tolerant(shutil.copystat)
shutil.copymode = _tolerant(shutil.copymode)
PY
echo "import nix_volume_fix" > "$site/nix_volume_fix.pth"
"$ACE_DIR/.venv/bin/python" -c 'import shutil; assert shutil.copystat.__name__ == "wrapper"'

printf '%s\n%s\n' "$(git -C "$ACE_DIR" rev-parse HEAD)" "$kind" > /opt/nix/ace-version
say "ACE-Step ready: $(head -c 12 /opt/nix/ace-version) [$kind]"
