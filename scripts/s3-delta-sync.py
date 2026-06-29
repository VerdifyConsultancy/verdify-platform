#!/usr/bin/env python3
"""s3-delta-sync.py — content-hash, change-gated delta uploader for the lab publisher.

WHY THIS EXISTS
---------------
`aws s3 sync` decides what to upload by comparing **size + mtime**. The Quartz
rebuild in `publish-site-content.sh` regenerates the *entire* `public/` tree on
every run, so every file gets a fresh mtime and `aws s3 sync` re-uploads the
whole ~400 MiB site to the HDD-backed S3 endpoint every 10 minutes — even when
the rendered bytes are identical. On a saturated endpoint that is pure waste.

WHAT THIS DOES
--------------
Drives uploads off a per-file SHA-256 manifest instead of mtime:

  1. walk LOCAL                       -> {relpath: sha256}
  2. load the manifest of what was last uploaded (PVC cache, else S3, else cold)
  3. CHANGE GATE: if nothing differs, upload zero objects and exit
  4. otherwise upload ONLY the changed/new files (one `aws s3 cp --recursive`
     over a hardlink staging tree containing just those files)
  5. with --delete, remove keys whose local file vanished
  6. persist the new manifest (PVC cache + S3) so the next run is a true delta

The manifest lives OUTSIDE the synced trees (its own `manifests/` prefix), so it
never feeds back into the walk. nginx serves the PVC directly; the S3 copy of
`public/` is a durable mirror only, so delta/skip behaviour is invisible to the
live site.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

MANIFEST_VERSION = 1
_READ_CHUNK = 1024 * 1024


def log(label: str, msg: str) -> None:
    print(f"[s3-delta-sync:{label}] {msg}", flush=True)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_READ_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def build_manifest(local: str) -> dict[str, str]:
    """Map each regular file under ``local`` to its content hash (relpath keys)."""
    manifest: dict[str, str] = {}
    # followlinks=False: never descend symlinked dirs (avoid loops / escaping the tree).
    for root, _dirs, files in os.walk(local, followlinks=False):
        for name in files:
            full = os.path.join(root, name)
            if not os.path.isfile(full):  # skip sockets, broken symlinks, etc.
                continue
            manifest[os.path.relpath(full, local)] = sha256_file(full)
    return manifest


def aws_argv(endpoint: str | None, *args: str) -> list[str]:
    argv = ["aws"]
    if endpoint:
        argv += ["--endpoint-url", endpoint]
    argv += list(args)
    return argv


def run_aws(endpoint: str | None, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        aws_argv(endpoint, *args),
        check=True,
        text=True,
        capture_output=True,
    )


def load_prev_manifest(local_manifest: str, manifest_uri: str, endpoint: str | None, label: str) -> dict[str, str]:
    """Prefer the PVC cache; fall back to S3 so a rescheduled (wiped) PVC still deltas."""
    if os.path.isfile(local_manifest):
        try:
            with open(local_manifest, encoding="utf-8") as fh:
                return dict(json.load(fh).get("files", {}))
        except (OSError, ValueError) as exc:
            log(label, f"local manifest unreadable ({exc}); falling back to S3")
    try:
        proc = run_aws(endpoint, "s3", "cp", manifest_uri, "-")
        return dict(json.loads(proc.stdout).get("files", {}))
    except subprocess.CalledProcessError:
        log(label, "no prior manifest in S3 — treating as cold start (full upload)")
    except ValueError as exc:
        log(label, f"S3 manifest unparseable ({exc}); treating as cold start")
    return {}


def stage_changed(local: str, changed: list[str], parent: str) -> str:
    """Hardlink (copy on cross-device) the changed files into a temp tree mirroring relpaths."""
    staging = tempfile.mkdtemp(prefix=".s3delta-", dir=parent)
    for rel in changed:
        src = os.path.join(local, rel)
        dst = os.path.join(staging, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
    return staging


def write_manifest(local_manifest: str, manifest_uri: str, endpoint: str | None, files: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(local_manifest) or ".", exist_ok=True)
    payload = json.dumps({"version": MANIFEST_VERSION, "files": files}, sort_keys=True)
    with open(local_manifest, "w", encoding="utf-8") as fh:
        fh.write(payload)
    run_aws(endpoint, "s3", "cp", local_manifest, manifest_uri, "--only-show-errors")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--local", required=True, help="local directory to mirror")
    p.add_argument("--remote", required=True, help="destination s3://bucket/prefix (no trailing slash needed)")
    p.add_argument("--manifest", required=True, help="s3:// URI of the per-tree manifest (outside the synced tree)")
    p.add_argument("--local-manifest", required=True, help="PVC path for the manifest cache")
    p.add_argument("--endpoint", default="", help="S3 endpoint URL (empty = AWS default resolution)")
    p.add_argument("--label", default="tree", help="log label for this tree (content/public/state)")
    p.add_argument("--delete", action="store_true", help="remove remote keys whose local file vanished")
    p.add_argument("--dry-run", action="store_true", help="report the delta without touching S3")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    label = args.label
    endpoint = args.endpoint or None
    remote = args.remote.rstrip("/")

    if not os.path.isdir(args.local):
        log(label, f"local dir missing: {args.local}")
        return 2

    new_files = build_manifest(args.local)
    prev_files = load_prev_manifest(args.local_manifest, args.manifest, endpoint, label)

    changed = sorted(rel for rel, h in new_files.items() if prev_files.get(rel) != h)
    deleted = sorted(rel for rel in prev_files if rel not in new_files) if args.delete else []

    total_mb = sum(os.path.getsize(os.path.join(args.local, r)) for r in changed) / (1024 * 1024)
    log(
        label,
        f"{len(new_files)} files; {len(changed)} changed/new (~{total_mb:.1f} MiB), {len(deleted)} to delete",
    )

    if not changed and not deleted:
        log(label, "no content changes — skipping upload (change gate)")
        return 0

    if args.dry_run:
        for rel in changed[:20]:
            log(label, f"  would upload {rel}")
        for rel in deleted[:20]:
            log(label, f"  would delete {rel}")
        if len(changed) + len(deleted) > 40:
            log(label, "  … (list truncated)")
        return 0

    staging = ""
    try:
        if changed:
            staging = stage_changed(args.local, changed, os.path.dirname(os.path.abspath(args.local)))
            log(label, f"uploading {len(changed)} object(s) to {remote}/")
            run_aws(endpoint, "s3", "cp", staging, remote, "--recursive", "--only-show-errors")
        for rel in deleted:
            run_aws(endpoint, "s3", "rm", f"{remote}/{rel}", "--only-show-errors")
        if deleted:
            log(label, f"pruned {len(deleted)} stale object(s) from {remote}/")
        write_manifest(args.local_manifest, args.manifest, endpoint, new_files)
    finally:
        if staging and os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)

    log(label, "delta upload complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
