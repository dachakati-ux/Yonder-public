#!/usr/bin/env bash
# Build an air-gapped USB pack for RHEL 8.10 (x86_64).
# Run on a machine WITH internet + Docker (linux/amd64 emulation OK on Apple Silicon).
#
# Usage:
#   bash packaging/offline/build-usb-pack.sh
#
# Output:
#   dist/yonder-offline-rhel8-amd64/
#   dist/yonder-offline-rhel8-amd64.tar.gz
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="${ROOT}/dist/yonder-offline-rhel8-amd64"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
NODE_VERSION="${NODE_VERSION:-22.14.0}"
UV_VERSION="${UV_VERSION:-0.6.14}"

log() { printf '[offline-pack] %s\n' "$*"; }
die() { printf '[offline-pack] ERROR: %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null || die "curl required"
command -v tar >/dev/null || die "tar required"
command -v rsync >/dev/null || die "rsync required"
command -v docker >/dev/null || die "docker required to produce linux/amd64 artifacts"

rm -rf "${OUT_DIR}"
mkdir -p "${OUT_DIR}/payload"/{python,node,uv,wheelhouse,requirements,repo,console,systemd,env,scripts,nvidia,system-rpms,model-bundles,cuda,runtimes,conversion-tools,vllm-sm70,diffusers-wheels}

log "Copying installers into pack root"
cp "${ROOT}/packaging/offline/install.sh" "${OUT_DIR}/install.sh"
cp "${ROOT}/packaging/offline/install-worker.sh" "${OUT_DIR}/install-worker.sh"
cp "${ROOT}/packaging/offline/install-nvidia.sh" "${OUT_DIR}/install-nvidia.sh"
cp "${ROOT}/packaging/offline/install-llama-runtime.sh" "${OUT_DIR}/install-llama-runtime.sh"
cp "${ROOT}/packaging/offline/uninstall.sh" "${OUT_DIR}/uninstall.sh"
cp "${ROOT}/packaging/offline/verify-install.sh" "${OUT_DIR}/verify-install.sh"
cp "${ROOT}/packaging/offline/verify-runtime-artifacts.sh" "${OUT_DIR}/verify-runtime-artifacts.sh"
cp "${ROOT}/packaging/offline/configure-firewall.sh" "${OUT_DIR}/configure-firewall.sh"
cp "${ROOT}/packaging/offline/configure-selinux.sh" "${OUT_DIR}/configure-selinux.sh"
cp "${ROOT}/packaging/offline/README.md" "${OUT_DIR}/README.md"
chmod +x "${OUT_DIR}/install.sh" "${OUT_DIR}/install-worker.sh" \
  "${OUT_DIR}/install-nvidia.sh" "${OUT_DIR}/install-llama-runtime.sh" \
  "${OUT_DIR}/uninstall.sh" "${OUT_DIR}/verify-runtime-artifacts.sh" \
  "${OUT_DIR}/verify-install.sh" "${OUT_DIR}/configure-firewall.sh" \
  "${OUT_DIR}/configure-selinux.sh"
cp "${ROOT}/packaging/offline/lib.sh" "${OUT_DIR}/payload/scripts/lib.sh"
cp "${ROOT}/packaging/offline/system-packages.base.txt" "${OUT_DIR}/payload/scripts/system-packages.base.txt"
cp "${ROOT}/packaging/offline/system-packages.nvidia.txt" "${OUT_DIR}/payload/scripts/system-packages.nvidia.txt"
mkdir -p "${OUT_DIR}/payload/systemd" "${OUT_DIR}/payload/env"
cp "${ROOT}/packaging/offline/systemd/"*.service "${OUT_DIR}/payload/systemd/" 2>/dev/null || true
# Templated unit names contain '@' — copy explicitly if glob missed them.
cp "${ROOT}/packaging/offline/systemd/yonder-llama-cpp@.service" \
  "${OUT_DIR}/payload/systemd/" 2>/dev/null || true
cp "${ROOT}/packaging/offline/env/"*.example "${OUT_DIR}/payload/env/"

log "Staging offline system RPMs (RHEL 8 / Rocky-compatible)"
SYSTEM_RPM_SRC="${ROOT}/vendor/rhel8-rpms/repo"
if [[ ! -d "${SYSTEM_RPM_SRC}" ]] || ! compgen -G "${SYSTEM_RPM_SRC}/*.rpm" >/dev/null; then
  die "Missing ${SYSTEM_RPM_SRC}/*.rpm — run: bash packaging/offline/fetch-system-rpms.sh"
fi
if [[ ! -f "${SYSTEM_RPM_SRC}/repodata/repomd.xml" ]]; then
  die "Missing createrepo metadata in ${SYSTEM_RPM_SRC}/repodata"
fi
rsync -a "${SYSTEM_RPM_SRC}/" "${OUT_DIR}/payload/system-rpms/"
SYSTEM_RPM_COUNT="$(find "${OUT_DIR}/payload/system-rpms" -maxdepth 1 -name '*.rpm' | wc -l | tr -d ' ')"
log "  staged ${SYSTEM_RPM_COUNT} system RPM(s) ($(du -sh "${OUT_DIR}/payload/system-rpms" | awk '{print $1}'))"

log "Staging offline model bundles (if present)"
MODEL_BUNDLE_SRC="${ROOT}/vendor/model-bundles"
MODEL_BUNDLE_COUNT=0
if [[ -d "${MODEL_BUNDLE_SRC}" ]]; then
  shopt -s nullglob
  for bundle_dir in "${MODEL_BUNDLE_SRC}"/*/; do
    if [[ -f "${bundle_dir}/bundle.yaml" ]]; then
      name="$(basename "${bundle_dir}")"
      rsync -a "${bundle_dir}" "${OUT_DIR}/payload/model-bundles/${name}/"
      MODEL_BUNDLE_COUNT=$((MODEL_BUNDLE_COUNT + 1))
      log "  + model bundle ${name}"
    fi
  done
  shopt -u nullglob
fi
if [[ "${MODEL_BUNDLE_COUNT}" -eq 0 ]]; then
  log "  (none — status=not_bundled; see docs/model-bundles.md)"
  printf 'status=not_bundled\n' > "${OUT_DIR}/payload/model-bundles/STATUS.txt"
else
  printf 'status=staged\ncount=%s\n' "${MODEL_BUNDLE_COUNT}" > "${OUT_DIR}/payload/model-bundles/STATUS.txt"
fi

log "Staging CUDA toolkit runfile (if present)"
CUDA_STAGED=0
mkdir -p "${ROOT}/vendor/cuda"
shopt -s nullglob
for runfile in "${ROOT}/vendor/cuda/"cuda_*_linux.run; do
  cp "${runfile}" "${OUT_DIR}/payload/cuda/cuda_12.6.3_linux.run"
  CUDA_STAGED=1
  log "  + $(basename "${runfile}")"
  break
done
shopt -u nullglob
if [[ "${CUDA_STAGED}" -eq 0 ]]; then
  log "  (none — install-llama-runtime.sh will skip toolkit)"
  printf 'status=not_bundled\n' > "${OUT_DIR}/payload/cuda/STATUS.txt"
else
  printf 'status=staged\n' > "${OUT_DIR}/payload/cuda/STATUS.txt"
fi

log "Staging llama.cpp sm_70 runtime tree (if present)"
LLAMA_RUNTIME_SRC="${ROOT}/vendor/runtimes/llama.cpp-b10167-cuda-sm70"
LLAMA_STAGED=0
if [[ -x "${LLAMA_RUNTIME_SRC}/bin/llama-server" ]]; then
  rsync -a "${LLAMA_RUNTIME_SRC}/" \
    "${OUT_DIR}/payload/runtimes/llama.cpp-b10167-cuda-sm70/"
  LLAMA_STAGED=1
  log "  + llama.cpp-b10167-cuda-sm70"
else
  log "  (none — copy from ai1 /opt/yonder/runtimes/... into vendor/runtimes/ before rebuild)"
  printf 'status=not_bundled\n' > "${OUT_DIR}/payload/runtimes/STATUS.txt"
fi
if [[ "${LLAMA_STAGED}" -eq 1 ]]; then
  printf 'status=staged\n' > "${OUT_DIR}/payload/runtimes/STATUS.txt"
fi

log "Staging optional verified runtime payload slots"
for slot in conversion-tools vllm-sm70 diffusers-wheels; do
  src="${ROOT}/vendor/${slot}"
  dest="${OUT_DIR}/payload/${slot}"
  if [[ -d "${src}" ]] && compgen -G "${src}/*" >/dev/null; then
    rsync -a "${src}/" "${dest}/"
    printf 'status=staged\nsource=vendor/%s\n' "${slot}" > "${dest}/STATUS.txt"
    log "  + ${slot}"
  else
    printf 'status=not_reported\nsource=vendor/%s\n' "${slot}" > "${dest}/STATUS.txt"
    log "  ${slot}=not_reported"
  fi
done

log "Staging NVIDIA driver RPMs (if present)"
NVIDIA_COUNT=0
mkdir -p "${ROOT}/vendor/nvidia"
# Prefer vendor/nvidia/, also pick up repo-root nvidia-*.rpm drops (dedupe by basename).
SEEN_NVIDIA=""
shopt -s nullglob
for rpm in "${ROOT}/vendor/nvidia/"*.rpm "${ROOT}/vendor/nvidia/"*.RPM \
           "${ROOT}/nvidia-"*.rpm "${ROOT}/nvidia-"*.RPM; do
  if [[ -f "${rpm}" ]]; then
    base="$(basename "${rpm}")"
    case " ${SEEN_NVIDIA} " in
      *" ${base} "*) continue ;;
    esac
    SEEN_NVIDIA="${SEEN_NVIDIA} ${base}"
    log "  + ${base} ($(du -h "${rpm}" | awk '{print $1}'))"
    cp -f "${rpm}" "${OUT_DIR}/payload/nvidia/"
    NVIDIA_COUNT=$((NVIDIA_COUNT + 1))
  fi
done
shopt -u nullglob
if [[ "${NVIDIA_COUNT}" -eq 0 ]]; then
  log "  (none found — place RPMs in vendor/nvidia/ or nvidia-*.rpm at repo root)"
else
  log "  staged ${NVIDIA_COUNT} RPM(s)"
fi

log "Staging repository payload"
mkdir -p "${OUT_DIR}/payload/repo"
for item in apps packages config database scripts docs pyproject.toml uv.lock README.md WORKLOG.md AGENTS.md; do
  if [[ -e "${ROOT}/${item}" ]]; then
    rsync -a --delete \
      --exclude '.venv' \
      --exclude 'node_modules' \
      --exclude '.next' \
      --exclude '__pycache__' \
      --exclude '*.pyc' \
      --exclude '.git' \
      "${ROOT}/${item}" "${OUT_DIR}/payload/repo/"
  fi
done

rsync -a \
  --exclude '.next' \
  --exclude 'node_modules' \
  "${ROOT}/apps/console/" "${OUT_DIR}/payload/console/"

STAGE="${OUT_DIR}/.stage"
mkdir -p "${STAGE}"

log "Fetching uv + Node linux x64 binaries on host"
curl -fsSL --retry 3 \
  -o "${STAGE}/uv.tar.gz" \
  "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz"
tar -xzf "${STAGE}/uv.tar.gz" -C "${STAGE}"
cp "${STAGE}/uv-x86_64-unknown-linux-gnu/uv" "${OUT_DIR}/payload/uv/uv"
chmod +x "${OUT_DIR}/payload/uv/uv"

curl -fsSL --retry 3 \
  -o "${STAGE}/node.tar.xz" \
  "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz"
tar -xJf "${STAGE}/node.tar.xz" -C "${STAGE}"
rm -rf "${OUT_DIR}/payload/node/runtime"
mv "${STAGE}/node-v${NODE_VERSION}-linux-x64" "${OUT_DIR}/payload/node/runtime"

log "Building Python runtime, wheels, and console inside Docker (linux/amd64)"
docker run --rm --platform linux/amd64 \
  -v "${ROOT}:/src:ro" \
  -v "${OUT_DIR}/payload:/out" \
  -e UV_VERSION="${UV_VERSION}" \
  -e PYTHON_VERSION="${PYTHON_VERSION}" \
  -w /src \
  python:3.12-bookworm \
  bash -lc '
    set -euo pipefail
    apt-get update -qq
    apt-get install -y -qq curl ca-certificates rsync >/dev/null
    curl -fsSL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" -o /tmp/uv.tgz
    tar -xzf /tmp/uv.tgz -C /tmp
    export PATH="/tmp/uv-x86_64-unknown-linux-gnu:$PATH"
    UV=uv

    # Standalone CPython matching the worker OS family.
    "$UV" python install "${PYTHON_VERSION}"
    PY="$("$UV" python find "${PYTHON_VERSION}")"
    PYHOME="$(cd "$(dirname "${PY}")/.." && pwd)"
    mkdir -p /out/python/python
    rsync -a "${PYHOME}/" /out/python/python/

    mkdir -p /out/wheelhouse /out/requirements /work
    rsync -a --exclude .venv --exclude node_modules --exclude .next \
      /src/apps /src/packages /src/pyproject.toml /src/uv.lock /src/README.md /work/
    cd /work

    "$UV" export --package yonder-gateway --no-dev --no-emit-workspace --no-hashes -o /out/requirements/gateway.txt
    "$UV" export --package yonder-worker --no-dev --no-emit-workspace --no-hashes -o /out/requirements/worker.txt

    "$UV" build --wheel --package yonder-contracts --out-dir /out/wheelhouse
    "$UV" build --wheel --package yonder-configuration --out-dir /out/wheelhouse
    "$UV" build --wheel --package yonder-telemetry --out-dir /out/wheelhouse
    "$UV" build --wheel --package yonder-gateway --out-dir /out/wheelhouse
    "$UV" build --wheel --package yonder-worker --out-dir /out/wheelhouse

    # Download third-party wheels for this linux/amd64 environment.
    /usr/local/bin/python -m pip install --upgrade pip
    /usr/local/bin/python -m pip download \
      --dest /out/wheelhouse \
      -r /out/requirements/gateway.txt \
      -r /out/requirements/worker.txt \
      hatchling setuptools wheel pip
  '

log "npm ci + build console (linux/amd64)"
docker run --rm --platform linux/amd64 \
  -v "${OUT_DIR}/payload/console:/app" \
  -w /app \
  -e NEXT_PUBLIC_YONDER_GATEWAY_URL=http://127.0.0.1:8080 \
  node:22-bookworm \
  bash -lc 'set -euo pipefail; npm ci; npm run build; npm prune --omit=dev || true'

cat > "${OUT_DIR}/VERSION" <<EOF
yonder_offline_pack=2
target_os=rhel8.10
arch=x86_64
python=${PYTHON_VERSION}
node=${NODE_VERSION}
uv=${UV_VERSION}
system_rpms=${SYSTEM_RPM_COUNT}
model_bundles=${MODEL_BUNDLE_COUNT}
cuda_toolkit=${CUDA_STAGED}
llama_runtime=${LLAMA_STAGED}
nvidia_rpms=${NVIDIA_COUNT}
built_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

(
  cd "${OUT_DIR}"
  find . -type f ! -path './.stage/*' | sort > MANIFEST.txt
  while IFS= read -r path; do
    sha256sum "${path}"
  done < MANIFEST.txt > MANIFEST.sha256
)

rm -rf "${STAGE}"
log "Creating tarball"
mkdir -p "${ROOT}/dist"
TAR="${ROOT}/dist/yonder-offline-rhel8-amd64.tar.gz"
rm -f "${TAR}"
tar -C "$(dirname "${OUT_DIR}")" -czf "${TAR}" "$(basename "${OUT_DIR}")"
BYTES="$(wc -c < "${TAR}" | tr -d ' ')"
log "Pack ready:"
log "  ${OUT_DIR}"
log "  ${TAR} (${BYTES} bytes)"
log "Copy to USB, unpack on RHEL, then:"
log "  sudo bash install.sh"
log "  sudo bash install-worker.sh            # CPU worker"
log "  sudo bash install-worker.sh --install-nvidia   # GPU worker"
if [[ "${SYSTEM_RPM_COUNT}" -gt 0 ]]; then
  log "System RPMs are in payload/system-rpms/ (${SYSTEM_RPM_COUNT} file(s))."
fi
if [[ "${NVIDIA_COUNT}" -gt 0 ]]; then
  log "NVIDIA RPMs are in payload/nvidia/ (${NVIDIA_COUNT} file(s))."
fi
