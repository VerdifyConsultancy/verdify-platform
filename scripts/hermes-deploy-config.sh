#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "The retired host-copy Hermes deploy path has been removed." >&2
echo "Validating GitOps desired state only; live delivery requires the gated prod Argo sync." >&2
exec make -C "$ROOT" hermes-deploy-config
