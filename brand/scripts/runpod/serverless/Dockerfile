# ACE-Step 1.5 with the studio's patch, as a RunPod Serverless load-balancing worker.
#
# Built from the repository root by .github/workflows/nix-ace-image.yml:
#   docker build -f brand/scripts/runpod/serverless/Dockerfile --build-arg ACE_PINNED=<ref> .
#
# The image holds the code and its Python environment. The models (about 10 GB) are not in it: they are
# downloaded once onto a RunPod global volume, mounted at /runpod-volume on every worker. See start.sh.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 PYTHONUTF8=1 \
    HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python UV_LINK_MODE=copy \
    ACE_DIR=/opt/ace-step

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ffmpeg curl ca-certificates libsndfile1 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# latest: ACE-Step's newest commit when the studio's patch applies to it and its environment installs; the tested
# commit (ACE_PINNED) otherwise. pinned: the tested commit only.
ARG ACE_MODE=latest
ARG ACE_PINNED
ARG ACE_REPO=https://github.com/ace-step/ACE-Step-1.5.git
COPY external/patches/ace-step.patch brand/scripts/runpod/serverless/build_ace.sh /opt/nix/
RUN bash /opt/nix/build_ace.sh "$ACE_MODE" "$ACE_PINNED" "$ACE_REPO" && rm -rf /root/.cache

COPY brand/scripts/runpod/serverless/start.sh brand/scripts/runpod/serverless/health.py /opt/nix/
RUN chmod +x /opt/nix/start.sh

# 8000: ACE-Step's API (PORT). 8080: the health answer the load balancer reads (PORT_HEALTH).
EXPOSE 8000 8080
CMD ["/opt/nix/start.sh"]
