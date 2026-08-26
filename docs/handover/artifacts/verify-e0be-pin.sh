#!/usr/bin/env bash
set -euo pipefail

base="e0be4e05edbbf54b954bb7f9f6a6a7bca91ffaaf"
candidate="${1:?usage: verify-e0be-pin.sh <candidate-sha>}"
pin_file="deploy/k8s/overlays/prod/kustomization.yaml"

git cat-file -e "${candidate}^{commit}"
test "$(git show -s --format=%P "$candidate")" = "$base"
test "$(git diff-tree --no-commit-id --name-only -r "$candidate")" = "$pin_file"

# The actuator may change exactly five existing digest scalar lines—no image
# names, registry targets, comments, ordering, or unrelated YAML structure.
mapfile -t pin_diff_lines < <(
  git diff --unified=0 --no-color "$base" "$candidate" -- "$pin_file" \
    | awk '/^--- / || /^\+\+\+ / { next } /^[+-]/ { print }'
)
test "${#pin_diff_lines[@]}" -eq 10
for line in "${pin_diff_lines[@]}"; do
  [[ "$line" =~ ^[-+][[:space:]]+digest:[[:space:]]sha256:[0-9a-f]{64}$ ]]
done

subject="$(git show -s --format=%s "$candidate")"
author_email="$(git show -s --format=%ae "$candidate")"
[[ "$subject" =~ ^gitops\(pin\):\ verdify\ images\ ${base:0:12}\ -\>\ zot\ digests\ sha256:[0-9a-f]{64}$ ]]
test "$author_email" = "ci@vallery.net"

declare -A before after
while IFS=$'\t' read -r name new_name digest; do
  before["$name"]="${new_name}|${digest}"
done < <(git show "${base}:${pin_file}" | yq '.images[] | [.name, .newName, .digest] | @tsv')
while IFS=$'\t' read -r name new_name digest; do
  after["$name"]="${new_name}|${digest}"
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
done < <(git show "${candidate}:${pin_file}" | yq '.images[] | [.name, .newName, .digest] | @tsv')

test "${#before[@]}" -eq "${#after[@]}"
changed=()
for name in "${!before[@]}"; do
  test -n "${after[$name]+present}"
  if [ "${before[$name]}" != "${after[$name]}" ]; then
    changed+=("$name")
  fi
done

actual="$(printf '%s\n' "${changed[@]}" | LC_ALL=C sort)"
expected="$(printf '%s\n' \
  ghcr.io/verdifyconsultancy/verdify-api \
  ghcr.io/verdifyconsultancy/verdify-experiment-v2-orchestrator \
  ghcr.io/verdifyconsultancy/verdify-ingestor \
  ghcr.io/verdifyconsultancy/verdify-mcp \
  ghcr.io/verdifyconsultancy/verdify-migrate \
  | LC_ALL=C sort)"
test "$actual" = "$expected"

for name in \
  ghcr.io/verdifyconsultancy/verdify-api \
  ghcr.io/verdifyconsultancy/verdify-experiment-v2-orchestrator \
  ghcr.io/verdifyconsultancy/verdify-ingestor \
  ghcr.io/verdifyconsultancy/verdify-mcp \
  ghcr.io/verdifyconsultancy/verdify-migrate; do
  expected_new_name="registry.vallery.net/verdifyconsultancy/${name##*/}"
  test "${after[$name]%%|*}" = "$expected_new_name"
done

migrate_key="ghcr.io/verdifyconsultancy/verdify-migrate"
migrate_digest="${after[$migrate_key]#*|}"
render_dir="$(mktemp -d)"
trap 'rm -rf -- "$render_dir"' EXIT
git archive "$candidate" | tar -x -C "$render_dir"
rendered="$(kustomize build "$render_dir/deploy/k8s/overlays/prod")"

restore_images="$(printf '%s\n' "$rendered" | yq 'select(.kind == "Job" and .metadata.name == "verdify-experiment-v2-restore-rehearsal") | [.spec.template.spec.initContainers[]?.image, .spec.template.spec.containers[]?.image] | .[]')"
migrate_images="$(printf '%s\n' "$rendered" | yq 'select(.kind == "Job" and .metadata.name == "verdify-migrate") | [.spec.template.spec.initContainers[]?.image, .spec.template.spec.containers[]?.image] | .[]')"
expected_migrate_ref="registry.vallery.net/verdifyconsultancy/verdify-migrate@${migrate_digest}"
restore_match_count="$(printf '%s\n' "$restore_images" | awk -v expected="$expected_migrate_ref" '$0 == expected { count++ } END { print count + 0 }')"
migrate_match_count="$(printf '%s\n' "$migrate_images" | awk -v expected="$expected_migrate_ref" '$0 == expected { count++ } END { print count + 0 }')"
test "$restore_match_count" -eq 1
test "$migrate_match_count" -eq 1

printf 'PIN GO %s\n' "$candidate"
printf 'changed images:\n%s\n' "$actual"
printf 'migrate parity: %s\n' "$expected_migrate_ref"
