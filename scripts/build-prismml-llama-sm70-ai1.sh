#!/usr/bin/env bash
# Build PrismML llama.cpp for V100 sm_70 on ai1 and install beside the pinned
# stock runtime. Does NOT replace llama.cpp-b10167-cuda-sm70.
#
# Run on ai1 (or: ssh ai1 'bash -s' < scripts/build-prismml-llama-sm70-ai1.sh)
#
# Env overrides:
#   SRC_DIR   clone/build tree (default /opt/yonder/src/llama.cpp-prismml)
#   PREFIX    install prefix (default /opt/yonder/runtimes/llama.cpp-prismml-cuda-sm70)
#   CUDA_HOME default /opt/cuda-12.6
#   JOBS      parallel build jobs (default nproc)
set -euo pipefail

SRC_DIR="${SRC_DIR:-/opt/yonder/src/llama.cpp-prismml}"
PREFIX="${PREFIX:-/opt/yonder/runtimes/llama.cpp-prismml-cuda-sm70}"
CUDA_HOME="${CUDA_HOME:-/opt/cuda-12.6}"
JOBS="${JOBS:-$(nproc)}"
REPO_URL="${REPO_URL:-https://github.com/PrismML-Eng/llama.cpp.git}"

log() { printf '[prismml-build] %s\n' "$*"; }
die() { printf '[prismml-build] ERROR: %s\n' "$*" >&2; exit 1; }

[[ -x "${CUDA_HOME}/bin/nvcc" ]] || die "nvcc missing under ${CUDA_HOME}"
# Prefer PATH cmake; fall back to leftover kit from prior llama.cpp builds on ai1.
if ! command -v cmake >/dev/null 2>&1; then
  for candidate in \
    /opt/yonder/yonder-llama-build.*/cmake-*-linux-x86_64/bin/cmake \
    /opt/yonder/tools/cmake/bin/cmake; do
    # shellcheck disable=SC2086
    for c in $candidate; do
      if [[ -x "$c" ]]; then
        export PATH="$(dirname "$c"):${PATH}"
        log "using cmake from $c"
        break 2
      fi
    done
  done
fi
command -v cmake >/dev/null || die "cmake required (install or place kit under /opt/yonder/tools/cmake)"
command -v git >/dev/null || die "git required"

# Lab hosts often cannot reach GitHub. Prefer a pre-synced tree
# (rsync from Mac) when SKIP_GIT_UPDATE=1 or when clone already exists and
# fetch fails.
if [[ ! -d "${SRC_DIR}/.git" ]]; then
  log "cloning ${REPO_URL} -> ${SRC_DIR}"
  mkdir -p "$(dirname "${SRC_DIR}")"
  git clone --depth 1 "${REPO_URL}" "${SRC_DIR}"
elif [[ "${SKIP_GIT_UPDATE:-0}" == "1" ]]; then
  log "SKIP_GIT_UPDATE=1; using existing ${SRC_DIR} @ $(git -C "${SRC_DIR}" rev-parse --short HEAD)"
else
  log "updating existing clone ${SRC_DIR}"
  if ! git -C "${SRC_DIR}" fetch --depth 1 origin; then
    log "git fetch failed (offline lab?); continuing with local tree"
  else
    git -C "${SRC_DIR}" reset --hard origin/HEAD || git -C "${SRC_DIR}" pull --ff-only
  fi
fi

COMMIT="$(git -C "${SRC_DIR}" rev-parse HEAD)"
log "commit=${COMMIT}"

# Prefer gcc-toolset-13 when present (RHEL 8 pattern used for b10167).
if [[ -f /opt/rh/gcc-toolset-13/enable ]]; then
  # shellcheck disable=SC1091
  source /opt/rh/gcc-toolset-13/enable
  log "using gcc-toolset-13 ($(gcc --version | head -1))"
fi

export PATH="${CUDA_HOME}/bin:${PATH}"
export LD_LIBRARY_PATH="${CUDA_HOME}/lib64:${LD_LIBRARY_PATH:-}"

BUILD_DIR="${SRC_DIR}/build-sm70"
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"
log "cmake configure CMAKE_CUDA_ARCHITECTURES=70"
# Bake $ORIGIN so installed binaries resolve colocated .so files on
# workers that lack the ai1 build tree (default RUNPATH is build-dir only).
cmake -S "${SRC_DIR}" -B "${BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=70 \
  -DCMAKE_CUDA_COMPILER="${CUDA_HOME}/bin/nvcc" \
  -DCMAKE_INSTALL_PREFIX="${PREFIX}" \
  -DCMAKE_INSTALL_RPATH='$ORIGIN' \
  -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_TOOLS=ON \
  -DLLAMA_BUILD_SERVER=ON

log "cmake build -j${JOBS}"
cmake --build "${BUILD_DIR}" -j"${JOBS}" --target llama-server llama-cli

log "install to ${PREFIX} (sudo)"
sudo mkdir -p "${PREFIX}/bin"
sudo install -m 0755 "${BUILD_DIR}/bin/llama-server" "${PREFIX}/bin/llama-server"
sudo install -m 0755 "${BUILD_DIR}/bin/llama-cli" "${PREFIX}/bin/llama-cli"
# Ship shared libs next to binaries when present
if compgen -G "${BUILD_DIR}/bin/*.so*" >/dev/null; then
  sudo cp -a "${BUILD_DIR}/bin/"*.so* "${PREFIX}/bin/" || true
fi
printf '%s\n' "${COMMIT}" | sudo tee "${PREFIX}/PRISMML_COMMIT" >/dev/null
sudo chown -R root:root "${PREFIX}"
sudo chmod -R a+rX "${PREFIX}"

log "installed $(${PREFIX}/bin/llama-server --version 2>&1 | head -3 || true)"
log "DONE commit=${COMMIT} prefix=${PREFIX}"
