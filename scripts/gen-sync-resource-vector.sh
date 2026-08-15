#!/bin/sh
# gen-sync-resource-vector.sh — render the prod overlay and emit the explicit
# ArgoCD operation resources vector for the #317 selective-scope workaround.
#
# PREPARATION TOOL ONLY (#605): it renders deploy/k8s/overlays/prod at the
# checked-out revision and prints every resource as an
# {group, kind, name, namespace} entry suitable for an operator-reviewed
# explicit `operation.sync.resources` list (docs/runbooks/laptop-operator.md §2,
# docs/runbooks/attended-convergence.md). It performs NO cluster access and
# initiates NOTHING.
#
# Usage: scripts/gen-sync-resource-vector.sh [json|yaml]   (default json)
set -eu

FMT="${1:-json}"
OVERLAY="deploy/k8s/overlays/prod"
DEST_NS="verdify-prod"

if command -v kustomize >/dev/null 2>&1; then
  RENDER="kustomize build ${OVERLAY}"
elif command -v kubectl >/dev/null 2>&1; then
  RENDER="kubectl kustomize ${OVERLAY}"
else
  echo "FATAL: need kustomize or kubectl on PATH" >&2
  exit 1
fi

$RENDER | python3 -c '
import sys, json

try:
    import yaml
except ImportError:
    sys.stderr.write("FATAL: python3-yaml required\n")
    sys.exit(1)

fmt = sys.argv[1]
dest_ns = sys.argv[2]
entries = []
for doc in yaml.safe_load_all(sys.stdin):
    if not doc or not isinstance(doc, dict):
        continue
    api = doc.get("apiVersion", "")
    group = api.split("/")[0] if "/" in api else ""
    kind = doc.get("kind", "")
    meta = doc.get("metadata", {})
    name = meta.get("name", "")
    # Cluster-scoped kinds carry no namespace; namespaced kinds default to the
    # destination namespace exactly as ArgoCD resolves them.
    cluster_scoped = kind in {
        "Namespace", "ClusterRole", "ClusterRoleBinding", "PriorityClass",
        "CustomResourceDefinition", "StorageClass", "ValidatingAdmissionPolicy",
        "ValidatingAdmissionPolicyBinding",
    }
    ns = "" if cluster_scoped else meta.get("namespace", dest_ns)
    entries.append({"group": group, "kind": kind, "name": name, "namespace": ns})

entries.sort(key=lambda e: (e["kind"], e["name"]))
if fmt == "yaml":
    sys.stdout.write(yaml.safe_dump({"resources": entries}, sort_keys=False))
else:
    json.dump({"resources": entries, "count": len(entries)}, sys.stdout, indent=2)
    sys.stdout.write("\n")
' "$FMT" "$DEST_NS"
