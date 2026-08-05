#!/usr/bin/env bash
# =============================================================================
# push_images.sh — Pull NIM from NVIDIA NGC, build the translator sidecar,
# and push both to your Snowflake image repository.
#
# Prerequisites:
#   - Docker Desktop running (linux/amd64 buildx available)
#   - snow CLI installed and logged in: snow connection test -c <YOUR_CONNECTION>
#   - NVIDIA NGC API key exported: export NGC_API_KEY="nvapi-..."
#   - 01_snowflake_setup.sql already executed in Snowflake
#
# Usage:
#   export NGC_API_KEY="nvapi-xxxxxxxxxxxx"
#   export SNOW_CONNECTION="JMARCISZEWSKI_AWS1"   # your snow CLI connection name
#   bash scripts/push_images.sh
# =============================================================================

set -euo pipefail

: "${NGC_API_KEY:?Set NGC_API_KEY to your NVIDIA NGC API key (nvapi-...)}"
: "${SNOW_CONNECTION:?Set SNOW_CONNECTION to your snow CLI connection name}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

NIM_IMAGE="nvcr.io/nim/nvidia/model-free-nim:latest"
TRANSLATOR_DIR="${REPO_ROOT}/docker/translator"

echo "=== Step 1: Login to NVIDIA NGC registry ==="
echo "${NGC_API_KEY}" | docker login nvcr.io -u '$oauthtoken' --password-stdin

echo ""
echo "=== Step 2: Pull Model-Free NIM (linux/amd64) ==="
# --platform ensures amd64 on Apple Silicon Macs — SPCS runs linux/amd64
docker pull --platform linux/amd64 "${NIM_IMAGE}"

echo ""
echo "=== Step 3: Get Snowflake image registry URL ==="
REGISTRY_URL=$(snow spcs image-registry url -c "${SNOW_CONNECTION}" 2>/dev/null | tr -d '"')
echo "Registry: ${REGISTRY_URL}"

echo ""
echo "=== Step 4: Login to Snowflake image registry ==="
snow spcs image-registry login -c "${SNOW_CONNECTION}"

NIM_TARGET="${REGISTRY_URL}/nemotron_db/nemotron_schema/nemotron_repo/model-free-nim:latest"
TRANSLATOR_TARGET="${REGISTRY_URL}/nemotron_db/nemotron_schema/nemotron_repo/nim-translator:latest"

echo ""
echo "=== Step 5: Tag and push NIM image ==="
docker tag "${NIM_IMAGE}" "${NIM_TARGET}"
docker push "${NIM_TARGET}"
echo "Pushed: ${NIM_TARGET}"

echo ""
echo "=== Step 6: Build translator sidecar (linux/amd64) ==="
docker build --platform linux/amd64 -t nim-translator:latest "${TRANSLATOR_DIR}"

echo ""
echo "=== Step 7: Tag and push translator image ==="
docker tag nim-translator:latest "${TRANSLATOR_TARGET}"
docker push "${TRANSLATOR_TARGET}"
echo "Pushed: ${TRANSLATOR_TARGET}"

echo ""
echo "=== Step 8: Upload service-spec.yaml to Snowflake stage ==="
snow stage copy "${REPO_ROOT}/service-spec.yaml" \
    "@NEMOTRON_DB.NEMOTRON_SCHEMA.NEMOTRON_STAGE/" \
    --overwrite -c "${SNOW_CONNECTION}"
echo "Uploaded: service-spec.yaml → @NEMOTRON_DB.NEMOTRON_SCHEMA.NEMOTRON_STAGE/"

echo ""
echo "=== Done! ==="
echo "Images and spec are in your Snowflake registry/stage."
echo "Next step: run setup/02_deploy_service.sql in Snowflake"
