#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_MANIFEST="${REPO_ROOT}/patches/openclaw-weixin/2.4.4-source.json"
OUTPUT_DIR="${OPENCLAW_WEIXIN_OUTPUT_DIR:-${REPO_ROOT}/.runtime/openclaw-weixin-patched}"
STAGING_DIR="$(mktemp -d "${TMPDIR:-/tmp}/clawtrade-weixin-build.XXXXXX")"
trap 'rm -rf "${STAGING_DIR}"' EXIT

read_manifest() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))[sys.argv[2]])' \
    "${SOURCE_MANIFEST}" "$1"
}

PACKAGE_NAME="$(read_manifest package)"
UPSTREAM_VERSION="$(read_manifest upstreamVersion)"
UPSTREAM_TAG="$(read_manifest upstreamTag)"
UPSTREAM_COMMIT="$(read_manifest upstreamCommit)"
UPSTREAM_REPOSITORY="$(read_manifest upstreamRepository)"
EXPECTED_INTEGRITY="$(read_manifest tarballIntegrity)"
PATCHED_VERSION="$(read_manifest patchedVersion)"
PATCH_FILE="${REPO_ROOT}/patches/openclaw-weixin/$(read_manifest patchFile)"
EXPECTED_PATCH_SHA="$(read_manifest patchSha256)"
ARTIFACT_NAME="tencent-weixin-openclaw-weixin-${PATCHED_VERSION}.tgz"
ARTIFACT_PATH="${OUTPUT_DIR}/${ARTIFACT_NAME}"
BUILD_MANIFEST="${OUTPUT_DIR}/build-manifest.json"

actual_patch_sha="$(sha256sum "${PATCH_FILE}" | awk '{print $1}')"
if [[ "${actual_patch_sha}" != "${EXPECTED_PATCH_SHA}" ]]; then
  echo "patch checksum mismatch: expected ${EXPECTED_PATCH_SHA}, got ${actual_patch_sha}" >&2
  exit 1
fi

verify_artifact_chain() {
  python3 - \
    "${SOURCE_MANIFEST}" \
    "${BUILD_MANIFEST}" \
    "${PATCH_FILE}" \
    "${ARTIFACT_PATH}" <<'PY'
import hashlib
import json
import pathlib
import sys
import tarfile

source_path, build_path, patch_path, artifact_path = map(pathlib.Path, sys.argv[1:])
for required in (source_path, build_path, patch_path, artifact_path):
    if not required.is_file():
        raise SystemExit(f"missing verified plugin input: {required}")

source = json.loads(source_path.read_text(encoding="utf-8"))
build = json.loads(build_path.read_text(encoding="utf-8"))
actual_patch_sha = hashlib.sha256(patch_path.read_bytes()).hexdigest()
actual_artifact_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()

checks = {
    "source patch filename": patch_path.name == source.get("patchFile"),
    "source patch sha": actual_patch_sha == source.get("patchSha256"),
    "build patch sha": build.get("patchSha256") == source.get("patchSha256"),
    "build artifact sha": actual_artifact_sha == build.get("artifactSha256"),
    "patched version": build.get("patchedVersion") == source.get("patchedVersion"),
    "upstream version": build.get("upstreamVersion") == source.get("upstreamVersion"),
    "upstream tag": build.get("upstreamTag") == source.get("upstreamTag"),
    "upstream commit": build.get("upstreamCommit") == source.get("upstreamCommit"),
    "upstream integrity": build.get("upstreamIntegrity") == source.get("tarballIntegrity"),
}
failed = [name for name, passed in checks.items() if not passed]
if failed:
    raise SystemExit("plugin artifact verification failed: " + ", ".join(failed))

with tarfile.open(artifact_path, "r:gz") as archive:
    package = json.load(archive.extractfile("package/package.json"))
if package.get("name") != source.get("package") or package.get("version") != source.get("patchedVersion"):
    raise SystemExit(
        f"plugin artifact identity mismatch: {package.get('name')}@{package.get('version')}"
    )

print(f"verified_plugin={artifact_path}")
print(f"sha256={actual_artifact_sha}")
PY
}

if [[ "${1:-}" == "--verify-artifact" ]]; then
  verify_artifact_chain
  exit 0
fi
if [[ $# -ne 0 ]]; then
  echo "usage: $0 [--verify-artifact]" >&2
  exit 2
fi

mkdir -p "${OUTPUT_DIR}" "${STAGING_DIR}/download" "${STAGING_DIR}/pack" "${STAGING_DIR}/package"
if [[ -n "${OPENCLAW_WEIXIN_UPSTREAM_TARBALL:-}" ]]; then
  cp "${OPENCLAW_WEIXIN_UPSTREAM_TARBALL}" "${STAGING_DIR}/download/upstream.tgz"
else
  npm pack "${PACKAGE_NAME}@${UPSTREAM_VERSION}" \
    --pack-destination "${STAGING_DIR}/download" \
    --silent >/dev/null
fi
UPSTREAM_TARBALL="$(find "${STAGING_DIR}/download" -maxdepth 1 -type f -name '*.tgz' -print -quit)"
if [[ -z "${UPSTREAM_TARBALL}" ]]; then
  echo "npm pack did not produce an upstream tarball" >&2
  exit 1
fi

python3 - "${UPSTREAM_TARBALL}" "${EXPECTED_INTEGRITY}" <<'PY'
import base64
import hashlib
import pathlib
import sys

tarball = pathlib.Path(sys.argv[1])
expected = sys.argv[2]
actual = "sha512-" + base64.b64encode(hashlib.sha512(tarball.read_bytes()).digest()).decode()
if actual != expected:
    raise SystemExit(f"tarball integrity mismatch: expected {expected}, got {actual}")
PY

SOURCE_DIR="${STAGING_DIR}/package"
tar -xzf "${UPSTREAM_TARBALL}" --strip-components=1 -C "${SOURCE_DIR}"
if [[ -n "${OPENCLAW_WEIXIN_SOURCE_DIR:-}" ]]; then
  SOURCE_REPOSITORY="${OPENCLAW_WEIXIN_SOURCE_DIR}"
else
  SOURCE_REPOSITORY="${STAGING_DIR}/source-repository"
  git clone --filter=blob:none --no-checkout "${UPSTREAM_REPOSITORY}" "${SOURCE_REPOSITORY}"
fi
actual_commit="$(git -C "${SOURCE_REPOSITORY}" rev-parse "${UPSTREAM_COMMIT}^{commit}")"
tag_commit="$(git -C "${SOURCE_REPOSITORY}" rev-parse "refs/tags/${UPSTREAM_TAG}^{commit}")"
if [[ "${actual_commit}" != "${UPSTREAM_COMMIT}" || "${tag_commit}" != "${UPSTREAM_COMMIT}" ]]; then
  echo "upstream tag/commit mismatch" >&2
  exit 1
fi
git -C "${SOURCE_REPOSITORY}" archive "${UPSTREAM_COMMIT}" tsconfig.json | tar -x -C "${SOURCE_DIR}"

python3 - "${SOURCE_DIR}/package.json" "${PACKAGE_NAME}" "${UPSTREAM_VERSION}" <<'PY'
import json
import sys

package = json.load(open(sys.argv[1], encoding="utf-8"))
if package.get("name") != sys.argv[2] or package.get("version") != sys.argv[3]:
    raise SystemExit(f"unexpected upstream package identity: {package.get('name')}@{package.get('version')}")
PY

patch -d "${SOURCE_DIR}" -p1 --forward --batch <"${PATCH_FILE}"
python3 - "${SOURCE_DIR}/package.json" "${PATCHED_VERSION}" <<'PY'
import json
import sys

actual = json.load(open(sys.argv[1], encoding="utf-8")).get("version")
if actual != sys.argv[2]:
    raise SystemExit(f"patched package version mismatch: expected {sys.argv[2]}, got {actual}")
PY

npm ci \
  --prefix "${SOURCE_DIR}" \
  --include=dev \
  --ignore-scripts \
  --legacy-peer-deps
npm exec --prefix "${SOURCE_DIR}" -- vitest run \
  --root "${SOURCE_DIR}" \
  src/replacement.test.ts \
  src/auth/login-qr-cancel.test.ts \
  --coverage.enabled=false
(
  cd "${SOURCE_DIR}"
  npm run typecheck
  npm run build
)
npm pack "${SOURCE_DIR}" --pack-destination "${STAGING_DIR}/pack" --silent >/dev/null

PACKED_TARBALL="$(find "${STAGING_DIR}/pack" -maxdepth 1 -type f -name '*.tgz' -print -quit)"
if [[ -z "${PACKED_TARBALL}" ]]; then
  echo "npm pack did not produce a patched tarball" >&2
  exit 1
fi
if [[ "$(basename "${PACKED_TARBALL}")" != "${ARTIFACT_NAME}" ]]; then
  echo "unexpected patched artifact filename: $(basename "${PACKED_TARBALL}")" >&2
  exit 1
fi
cp "${PACKED_TARBALL}" "${ARTIFACT_PATH}"
ARTIFACT_SHA="$(sha256sum "${ARTIFACT_PATH}" | awk '{print $1}')"

python3 - \
  "${BUILD_MANIFEST}" \
  "${PACKAGE_NAME}" \
  "${UPSTREAM_VERSION}" \
  "${UPSTREAM_TAG}" \
  "${UPSTREAM_COMMIT}" \
  "${EXPECTED_INTEGRITY}" \
  "${PATCHED_VERSION}" \
  "${EXPECTED_PATCH_SHA}" \
  "${ARTIFACT_PATH}" \
  "${ARTIFACT_SHA}" <<'PY'
import json
import pathlib
import platform
import sys

output = pathlib.Path(sys.argv[1])
output.write_text(json.dumps({
    "package": sys.argv[2],
    "upstreamVersion": sys.argv[3],
    "upstreamTag": sys.argv[4],
    "upstreamCommit": sys.argv[5],
    "upstreamIntegrity": sys.argv[6],
    "patchedVersion": sys.argv[7],
    "patchSha256": sys.argv[8],
    "artifactPath": sys.argv[9],
    "artifactSha256": sys.argv[10],
    "pythonVersion": platform.python_version(),
    "verification": {
        "focusedTests": "passed",
        "typecheck": "passed",
        "build": "passed"
    }
}, indent=2) + "\n", encoding="utf-8")
PY

verify_artifact_chain

echo "patched_plugin=${ARTIFACT_PATH}"
echo "sha256=${ARTIFACT_SHA}"
