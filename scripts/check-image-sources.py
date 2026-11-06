#!/usr/bin/env python3
"""Every Verdify image comes from the estate registry (#1363; Jason 2026-11-05: "Every image
goes through the zot cache or origin").

Three checks:

1. Rendered: every container and init container in the three Argo sources
   (deploy/k8s/overlays/prod, deploy/k8s/dashboard, deploy/k8s/descheduler) is
   `registry.vallery.net/<repo>[:<tag>]@sha256:<64 hex>`. Third-party images are custody copies
   (`registry.vallery.net/jvallery/mirror-*`, jvallery/storage-infra deploy/registry-origin/custody).
2. Source: every `image:`/`imageName:` value in deploy/k8s YAML is a registry.vallery.net ref, a
   name a kustomize `images:` entry or ImageTagTransformer rewrites, or the activation placeholder.
3. Builds: every external `FROM` in a tracked Dockerfile (an earlier stage name excepted, `ARG`
   defaults resolved) is pinned by `@sha256:<64 hex>`. Routing base pulls through the estate cache
   is jvallery/agents#5374 (Kaniko `--registry-mirror`), not a source rewrite here.

PENDING lists refs that may still render with the issue that removes them; UNRENDERED lists
files no Argo source renders. Both only shrink.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy/k8s"
RENDERED_SOURCES = ("deploy/k8s/overlays/prod", "deploy/k8s/dashboard", "deploy/k8s/descheduler")
LOCAL = re.compile(
    r"^registry\.vallery\.net/[a-z0-9]+(?:[._/-][a-z0-9]+)*(?::[A-Za-z0-9._-]{1,128})?@sha256:[0-9a-f]{64}$"
)
PLACEHOLDER = "invalid.local/"
# Upstream repository prefix -> the issue that removes it. Shrink only.
PENDING: dict[str, str] = {}
# Files that no Argo source renders. Shrink only (delete the file or repin it).
UNRENDERED = {
    "deploy/k8s/components/umami/umami.yaml": "umami is not wired into any overlay (docs/SERVICE_MAP.md)",
    "deploy/k8s/components/twin-builder/twin-builder.yaml": "manual verdify-twin build Job, not rendered",
    "deploy/k8s/diagnostics/device-vlan-spike.yaml": "one-off diagnostic Pod, not rendered",
    "deploy/k8s/cnpg/dev/10-cluster.yaml": "decommissioned CNPG dev validation cluster",
}
POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "ReplicaSet", "Pod"}
DIGEST_PIN = re.compile(r"@sha256:[0-9a-f]{64}$")
ARG_LINE = re.compile(r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)\s*$", re.IGNORECASE)
VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
IMAGE_LINE = re.compile(r"""^\s*(?:-\s*)?(?:image|imageName):\s*["']?([^\s"'#]+)""")


def pod_spec(doc: dict) -> dict | None:
    kind, spec = doc.get("kind"), doc.get("spec") or {}
    if kind == "CronJob":
        return ((spec.get("jobTemplate") or {}).get("spec") or {}).get("template", {}).get("spec")
    if kind == "Pod":
        return spec
    if kind in POD_KINDS:
        return (spec.get("template") or {}).get("spec")
    return None


def rendered_refs(docs: list[dict]) -> list[tuple[str, str]]:
    refs = []
    for doc in docs:
        spec = pod_spec(doc)
        if not spec:
            continue
        name = f"{doc['kind']}/{doc['metadata']['name']}"
        for container in (*spec.get("initContainers", []), *spec.get("containers", [])):
            refs.append((f"{name}/{container['name']}", container.get("image", "")))
    return refs


def rendered_errors(refs: list[tuple[str, str]]) -> list[str]:
    errors = []
    for where, image in refs:
        if LOCAL.match(image) or any(image.startswith(prefix) for prefix in PENDING):
            continue
        errors.append(f"{where}: {image!r} is not registry.vallery.net/...@sha256:<digest>")
    return errors


def rewrite_names(root: Path = DEPLOY) -> set[str]:
    names = set()
    for path in root.rglob("*.y*ml"):
        try:
            docs = list(yaml.safe_load_all(path.read_text()))
        except yaml.YAMLError:
            continue
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if doc.get("kind") == "ImageTagTransformer":
                names.add((doc.get("imageTag") or {}).get("name", ""))
            for entry in doc.get("images") or []:
                if isinstance(entry, dict) and entry.get("name"):
                    names.add(entry["name"])
    names.discard("")
    return names


def source_errors(root: Path = DEPLOY, repo_root: Path = ROOT) -> list[str]:
    names = rewrite_names(root)
    errors = []
    for path in sorted(root.rglob("*.y*ml")):
        rel = path.relative_to(repo_root).as_posix()
        if rel in UNRENDERED:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            match = IMAGE_LINE.match(line)
            if not match:
                continue
            image = match.group(1)
            if image.startswith("registry.vallery.net/") or image.startswith(PLACEHOLDER) or image in names:
                continue
            if any(image.startswith(prefix) for prefix in PENDING):
                continue
            errors.append(f"{rel}:{number}: {image!r} is not an estate registry ref or a kustomize-rewritten name")
    return errors


def from_errors(name: str, text: str) -> list[str]:
    """External FROM images in one Dockerfile that are not digest-pinned."""
    args: dict[str, str] = {}
    stages: set[str] = set()
    errors = []
    for number, line in enumerate(text.splitlines(), 1):
        arg = ARG_LINE.match(line)
        if arg:
            args.setdefault(arg.group(1), arg.group(2).strip("\"'"))
            continue
        words = [w for w in line.split() if not w.startswith("--")]
        if not words or words[0].upper() != "FROM" or len(words) < 2:
            continue
        image = VAR.sub(lambda m: args.get(m.group(1), m.group(0)), words[1])
        if image.lower() not in stages and image != "scratch" and not DIGEST_PIN.search(image):
            errors.append(f"{name}:{number}: FROM {image!r} is not pinned by @sha256:<digest>")
        if len(words) >= 4 and words[2].upper() == "AS":
            stages.add(words[3].lower())
    return errors


def dockerfile_errors(repo_root: Path = ROOT) -> list[str]:
    out = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo_root, text=True)
    errors = []
    for name in sorted(n for n in out.split("\0") if n):
        base = Path(name).name
        if not (base.startswith("Dockerfile") or base.endswith(".Dockerfile")) or base.endswith(".dockerignore"):
            continue
        errors += from_errors(name, (repo_root / name).read_text())
    return errors


def render(path: str) -> list[dict]:
    tool = ["kustomize", "build"] if shutil.which("kustomize") else ["kubectl", "kustomize"]
    out = subprocess.check_output([*tool, path], cwd=ROOT, text=True)
    return [doc for doc in yaml.safe_load_all(out) if doc]


def main() -> int:
    errors = []
    for source in RENDERED_SOURCES:
        errors += [f"{source}: {e}" for e in rendered_errors(rendered_refs(render(source)))]
    errors += source_errors()
    errors += dockerfile_errors()
    for path in UNRENDERED:
        if not (ROOT / path).exists():
            errors.append(f"UNRENDERED lists {path}, which no longer exists: remove the entry")
    if errors:
        sys.stderr.write("Images must come from the estate registry (#1363):\n  " + "\n  ".join(errors) + "\n")
        return 1
    print(f"image sources OK ({', '.join(RENDERED_SOURCES)} + deploy/k8s source refs + Dockerfile FROMs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
