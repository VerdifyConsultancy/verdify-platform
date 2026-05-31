#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

: "${SERVICE_NAME:?Set SERVICE_NAME}"
: "${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
: "${GOOGLE_CLOUD_REGION:?Set GOOGLE_CLOUD_REGION}"

SOURCE_DIR="${SOURCE_DIR:-${ROOT_DIR}}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-}"
VPC_CONNECTOR="${VPC_CONNECTOR:-}"
VPC_EGRESS="${VPC_EGRESS:-private-ranges-only}"
ENV_VARS_FILE="${ENV_VARS_FILE:-${ROOT_DIR}/cloudrun/env.example.yaml}"
SECRETS_SPEC="${SECRETS_SPEC:-}"
MAX_INSTANCES="${MAX_INSTANCES:-3}"
MIN_INSTANCES="${MIN_INSTANCES:-0}"
MEMORY="${MEMORY:-1Gi}"
CPU="${CPU:-1}"
CONCURRENCY="${CONCURRENCY:-20}"
TIMEOUT="${TIMEOUT:-300}"

cmd=(
  gcloud run deploy "${SERVICE_NAME}"
  --project "${GOOGLE_CLOUD_PROJECT}"
  --region "${GOOGLE_CLOUD_REGION}"
  --source "${SOURCE_DIR}"
  --port 8080
  --cpu "${CPU}"
  --memory "${MEMORY}"
  --concurrency "${CONCURRENCY}"
  --timeout "${TIMEOUT}"
  --min-instances "${MIN_INSTANCES}"
  --max-instances "${MAX_INSTANCES}"
  --no-allow-unauthenticated
  --set-env-vars "PORT=8080"
)

if [[ -n "${ENV_VARS_FILE}" ]]; then
  cmd+=(--env-vars-file "${ENV_VARS_FILE}")
fi

if [[ -n "${SECRETS_SPEC}" ]]; then
  cmd+=(--set-secrets "${SECRETS_SPEC}")
fi

if [[ -n "${SERVICE_ACCOUNT}" ]]; then
  cmd+=(--service-account "${SERVICE_ACCOUNT}")
fi

if [[ -n "${VPC_CONNECTOR}" ]]; then
  cmd+=(--vpc-connector "${VPC_CONNECTOR}" --vpc-egress "${VPC_EGRESS}")
fi

"${cmd[@]}"
