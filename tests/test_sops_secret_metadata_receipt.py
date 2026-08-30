from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/sops-secret-metadata-receipt.py"


def _module():
    spec = importlib.util.spec_from_file_location("sops_secret_metadata_receipt_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _source(module, *, encrypted: bool = True):
    value = "ENC[AES256_GCM,data:ciphertext,iv:opaque,tag:opaque,type:str]" if encrypted else "plaintext-secret"
    document = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "verdify-hermes", "namespace": "verdify-prod"},
        "type": "Opaque",
        "data": {"OPENAI_API_KEY": value, "VERDIFY_MCP_TOKEN": value},
        "sops": {
            "age": [{"recipient": "age1publicmetadataonly", "enc": "opaque"}],
            "lastmodified": "2026-08-29T00:00:00Z",
            "mac": "ENC[AES256_GCM,data:mac,iv:opaque,tag:opaque,type:str]",
            "encrypted_regex": "^(data|stringData)$",
            "version": "3.10.2",
        },
    }
    return module.GitSource(
        Path("/work/agents"),
        Path("platform/gitops/secrets-ksops/verdify-prod/secret-verdify-hermes.enc.yaml"),
        "a" * 40,
        "b" * 40,
        "2026-08-29T00:00:00Z",
        yaml.safe_dump(document).encode(),
    )


def test_receipt_reports_only_source_target_key_names_and_sops_metadata() -> None:
    module = _module()
    source = _source(module)
    receipt = module.secret_metadata(
        source,
        expected_name="verdify-hermes",
        expected_namespace="verdify-prod",
        required_keys={"OPENAI_API_KEY", "VERDIFY_MCP_TOKEN"},
    )
    encoded = json.dumps(receipt, sort_keys=True)
    assert receipt["status"] == "pass"
    assert receipt["secret"]["required_keys_present"] is True
    assert receipt["secret"]["key_names"] == ["OPENAI_API_KEY", "VERDIFY_MCP_TOKEN"]
    assert receipt["sops"]["age_recipient_count"] == 1
    assert "ENC[" not in encoded
    assert "ciphertext" not in encoded
    assert "age1publicmetadataonly" not in encoded


def test_receipt_rejects_plaintext_without_quoting_it() -> None:
    module = _module()
    source = _source(module, encrypted=False)
    with pytest.raises(module.ReceiptError) as excinfo:
        module.secret_metadata(
            source,
            expected_name="verdify-hermes",
            expected_namespace="verdify-prod",
            required_keys={"OPENAI_API_KEY"},
        )
    assert "unencrypted value" in str(excinfo.value)
    assert "plaintext-secret" not in str(excinfo.value)


def test_receipt_rejects_wrong_target_or_missing_required_key_without_listing_values() -> None:
    module = _module()
    source = _source(module)
    with pytest.raises(module.ReceiptError, match="target does not match"):
        module.secret_metadata(
            source,
            expected_name="other-secret",
            expected_namespace="verdify-prod",
            required_keys=set(),
        )
    with pytest.raises(module.ReceiptError, match="missing one or more required key"):
        module.secret_metadata(
            source,
            expected_name="verdify-hermes",
            expected_namespace="verdify-prod",
            required_keys={"MISSING_KEY"},
        )


def test_receipt_requires_exact_sops_encryption_boundary() -> None:
    module = _module()
    source = _source(module)
    document = yaml.safe_load(source.contents)
    document["sops"]["encrypted_regex"] = "^(password)$"
    source = module.GitSource(
        source.repo_root,
        source.relative_path,
        source.revision,
        source.last_changed_revision,
        source.commit_time,
        yaml.safe_dump(document).encode(),
    )
    with pytest.raises(module.ReceiptError, match="encrypted_regex"):
        module.secret_metadata(
            source,
            expected_name="verdify-hermes",
            expected_namespace="verdify-prod",
            required_keys={"OPENAI_API_KEY"},
        )


def test_git_source_reads_the_exact_committed_revision(tmp_path) -> None:
    module = _module()
    repository = tmp_path / "agents"
    repository.mkdir()
    relative = Path("platform/gitops/secrets-ksops/verdify-prod/secret-verdify-hermes.enc.yaml")
    encrypted = repository / relative
    encrypted.parent.mkdir(parents=True)
    encrypted.write_bytes(_source(module).contents)
    for command in (
        ("init", "-q"),
        ("config", "user.name", "Receipt Test"),
        ("config", "user.email", "receipt@example.invalid"),
        ("add", relative.as_posix()),
        ("commit", "-qm", "test encrypted source"),
    ):
        subprocess.run(["git", "-C", str(repository), *command], check=True)

    source = module.git_source(relative, repo=repository, revision="HEAD")
    assert source.contents == encrypted.read_bytes()
    assert source.relative_path == relative
    assert len(source.revision) == len(source.last_changed_revision) == 40
