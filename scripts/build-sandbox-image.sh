#!/usr/bin/env bash
# Build the bsnexus-sandbox toolchain image and load it INTO the DinD
# sidecar. A plain `docker build` produces the image on the host
# daemon; the per-project sandboxes run inside the DinD, whose daemon
# cannot see host images — so the image must be `docker save | docker
# load`-ed across.
#
# Idempotent: safe to run on every deploy. Re-running rebuilds and
# reloads (cheap when layers are cached).
#
# Env:
#   DOCKER_HOST   DinD endpoint, e.g. tcp://bsnexus-sandbox-dind:2375
#                 (required — without it the image only lands on the
#                 host daemon and the sandboxes can't start)
#   SANDBOX_IMAGE image tag (default: bsnexus-sandbox:latest)
set -euo pipefail

IMAGE="${SANDBOX_IMAGE:-bsnexus-sandbox:latest}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKERFILE="${SCRIPT_DIR}/../deploy/sandbox/Dockerfile.sandbox"

if [[ -z "${DOCKER_HOST:-}" ]]; then
  echo "ERROR: DOCKER_HOST must point at the DinD sidecar (tcp://...:2375)." >&2
  echo "       Without it the image lands on the host daemon only." >&2
  exit 1
fi

echo "==> Building ${IMAGE} on the host daemon"
# Build on the host daemon (unset DOCKER_HOST for the build step).
env -u DOCKER_HOST docker build -f "${DOCKERFILE}" -t "${IMAGE}" "${SCRIPT_DIR}/.."

echo "==> Loading ${IMAGE} into the DinD daemon at ${DOCKER_HOST}"
env -u DOCKER_HOST docker save "${IMAGE}" | docker load

echo "==> Done. ${IMAGE} is available inside the DinD."
