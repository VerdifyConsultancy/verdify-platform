#!/usr/bin/env python3
"""Emit a SOPS Secret receipt without decrypting or exposing ciphertext values."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

MAX_SOURCE_BYTES = 4 * 1024 * 1024
SOPS_ENCRYPTED_REGEX = "^(data|stringData)$"
ENC_PREFIX = "ENC[AES256_GCM,"


class ReceiptError(RuntimeError):
    """Metadata-only receipt failure; messages never contain document values."""


@dataclass(frozen=True)
class GitSource:
    repo_root: Path
    relative_path: Path
    revision: str
    last_changed_revision: str
    commit_time: str
    contents: bytes


def _git(repo: Path, *args: str, text: bool = True):
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=text,
    )
    if result.returncode != 0:
        raise ReceiptError(f"git metadata lookup failed: {args[0]}")
    return result.stdout


def git_source(path: Path, *, repo: Path, revision: str) -> GitSource:
    repo_root = Path(_git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    candidate = path if path.is_absolute() else repo_root / path
    try:
        relative_path = candidate.resolve().relative_to(repo_root)
    except ValueError:
        raise ReceiptError("encrypted source path is outside the selected repository")
    resolved_revision = _git(repo_root, "rev-parse", f"{revision}^{{commit}}").strip()
    last_changed = _git(repo_root, "log", "-1", "--format=%H", resolved_revision, "--", str(relative_path)).strip()
    if not last_changed:
        raise ReceiptError("encrypted source has no commit at the selected revision")
    commit_time = _git(repo_root, "show", "-s", "--format=%cI", resolved_revision).strip()
    contents = _git(repo_root, "show", f"{resolved_revision}:{relative_path.as_posix()}", text=False)
    if not contents or len(contents) > MAX_SOURCE_BYTES:
        raise ReceiptError("encrypted source is empty or exceeds the receipt size bound")
    return GitSource(repo_root, relative_path, resolved_revision, last_changed, commit_time, contents)


def _encrypted_key_names(document: dict) -> list[str]:
    names: set[str] = set()
    for field in ("data", "stringData"):
        values = document.get(field)
        if values is None:
            continue
        if not isinstance(values, dict):
            raise ReceiptError(f"Secret {field} must be a mapping")
        for key, value in values.items():
            if not isinstance(key, str) or not key:
                raise ReceiptError(f"Secret {field} contains an invalid key name")
            if not isinstance(value, str) or not value.startswith(ENC_PREFIX):
                raise ReceiptError(f"Secret {field} contains an unencrypted value")
            names.add(key)
    if not names:
        raise ReceiptError("Secret contains no encrypted data or stringData keys")
    return sorted(names)


def _age_recipient_count(sops: dict) -> int:
    recipients: list[str] = []
    direct = sops.get("age", [])
    if isinstance(direct, list):
        recipients.extend(row.get("recipient") for row in direct if isinstance(row, dict))
    groups = sops.get("key_groups", [])
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("age"), list):
                continue
            recipients.extend(row.get("recipient") for row in group["age"] if isinstance(row, dict))
    return len({recipient for recipient in recipients if isinstance(recipient, str) and recipient.startswith("age1")})


def secret_metadata(source: GitSource, *, expected_name: str, expected_namespace: str, required_keys: set[str]) -> dict:
    try:
        document = yaml.safe_load(source.contents)
    except yaml.YAMLError:
        raise ReceiptError("encrypted source is not valid YAML")
    if not isinstance(document, dict) or document.get("apiVersion") != "v1" or document.get("kind") != "Secret":
        raise ReceiptError("encrypted source is not a Kubernetes v1 Secret")
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise ReceiptError("encrypted Secret metadata is absent")
    name = metadata.get("name")
    namespace = metadata.get("namespace")
    if name != expected_name or namespace != expected_namespace:
        raise ReceiptError("encrypted Secret target does not match the expected namespace/name")

    key_names = _encrypted_key_names(document)
    missing = sorted(required_keys - set(key_names))
    if missing:
        raise ReceiptError("encrypted Secret is missing one or more required key names")

    sops = document.get("sops")
    if not isinstance(sops, dict):
        raise ReceiptError("SOPS metadata is absent")
    if sops.get("encrypted_regex") != SOPS_ENCRYPTED_REGEX:
        raise ReceiptError("SOPS encrypted_regex does not protect data and stringData exactly")
    if not isinstance(sops.get("mac"), str) or not sops["mac"].startswith("ENC["):
        raise ReceiptError("SOPS encrypted MAC metadata is absent")
    age_recipients = _age_recipient_count(sops)
    if age_recipients < 1:
        raise ReceiptError("SOPS age recipient metadata is absent")
    version = sops.get("version")
    lastmodified = sops.get("lastmodified")
    if not isinstance(version, str) or not version:
        raise ReceiptError("SOPS version metadata is absent")
    if not isinstance(lastmodified, str) or not lastmodified:
        raise ReceiptError("SOPS lastmodified metadata is absent")

    return {
        "schema": "verdify.sops-secret-metadata-receipt/v1",
        "checked_at": datetime.now(UTC).isoformat(),
        "status": "pass",
        "source": {
            "repository": source.repo_root.name,
            "path": source.relative_path.as_posix(),
            "revision": source.revision,
            "revision_committed_at": source.commit_time,
            "last_changed_revision": source.last_changed_revision,
            "encrypted_file_sha256": hashlib.sha256(source.contents).hexdigest(),
        },
        "secret": {
            "namespace": namespace,
            "name": name,
            "type": document.get("type", "Opaque"),
            "key_names": key_names,
            "required_key_names": sorted(required_keys),
            "required_keys_present": True,
        },
        "sops": {
            "version": version,
            "lastmodified": lastmodified,
            "encrypted_regex": sops["encrypted_regex"],
            "encrypted_mac_present": True,
            "age_recipient_count": age_recipients,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="path to the encrypted Secret inside its Git repository")
    parser.add_argument(
        "--repo", type=Path, required=True, help="owning Git repository (no remote credentials emitted)"
    )
    parser.add_argument("--revision", default="HEAD", help="commit/ref whose encrypted bytes are authoritative")
    parser.add_argument("--expected-name", required=True)
    parser.add_argument("--expected-namespace", required=True)
    parser.add_argument("--required-key", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = git_source(args.source, repo=args.repo, revision=args.revision)
    receipt = secret_metadata(
        source,
        expected_name=args.expected_name,
        expected_namespace=args.expected_namespace,
        required_keys=set(args.required_key),
    )
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReceiptError as exc:
        print(
            json.dumps({"schema": "verdify.sops-secret-metadata-receipt/v1", "status": "fail", "error": str(exc)}),
            file=sys.stderr,
        )
        raise SystemExit(1)
