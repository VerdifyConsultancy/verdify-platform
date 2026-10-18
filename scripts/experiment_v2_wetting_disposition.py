"""Source-bound #778 evidence for #749. Reading a disposition grants no authority.

Exact reviewed receipt files in the accepted source manifest provide custody
only. Criterion-specific physical measurement validators are not yet available;
all non-null receipts remain explicitly unsupported and denied. Closing #778, a boolean, an
arbitrary file, a firmware version string, or historical source tests cannot
satisfy a physical criterion. A future manifest update must retain the actual
native measurement/qualification receipts, not manufacture reviewer assertions.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

POLICY_PATH = "research/planner-efficacy/wetting-incident-778-readiness.json"
SCHEMA = "verdify-wetting-readiness-disposition-v1"
CORRECTION = "5e22ca6436d512b82aa9dc6269eb7f221661215a"
DEVICE = "vallery/greenhouse-controller"
CRITERIA = frozenset(
    {
        "firmware_binary_lineage",
        "safe_fog_admission_and_rails",
        "physical_equipment_qualification",
        "counter_scope_and_reset_boundaries",
    }
)
SHA = re.compile(r"[0-9a-f]{64}")


def _read(root: Path, descriptor: object) -> bytes:
    if not isinstance(descriptor, dict) or set(descriptor) != {"path", "sha256"}:
        raise ValueError("wetting evidence requires an exact path/hash descriptor")
    path = descriptor["path"]
    if not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts:
        raise ValueError("wetting evidence path escapes source")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("wetting evidence path escapes source")
    data = resolved.read_bytes()
    if hashlib.sha256(data).hexdigest() != descriptor["sha256"]:
        raise ValueError("wetting evidence hash mismatch")
    return data


def collect(root: Path, *, application_source: str, experiment_id: str) -> dict:
    data = (root / POLICY_PATH).read_bytes()
    policy = json.loads(data)
    if not isinstance(policy, dict) or set(policy) != {
        "schema",
        "incident_issue",
        "device_id",
        "incident_started_at",
        "incident_ended_at",
        "source_correction_commit",
        "source_analysis",
        "criteria",
    }:
        raise ValueError("wetting disposition shape mismatch")
    if (
        policy["schema"],
        policy["incident_issue"],
        policy["device_id"],
        policy["incident_started_at"],
        policy["incident_ended_at"],
        policy["source_correction_commit"],
    ) != (
        SCHEMA,
        778,
        DEVICE,
        "2026-09-04T20:30:00Z",
        "2026-09-05T00:45:00Z",
        CORRECTION,
    ):
        raise ValueError("wetting incident/source identity mismatch")
    _read(root, policy["source_analysis"])
    if not isinstance(policy["criteria"], dict) or set(policy["criteria"]) != CRITERIA:
        raise ValueError("wetting criterion set mismatch")
    evidence = {}
    for name, descriptor in policy["criteria"].items():
        evidence[name] = None if descriptor is None else json.loads(_read(root, descriptor))
    return {
        "schema": SCHEMA,
        "application_source_revision": application_source,
        "experiment_id": experiment_id,
        "manifest_sha256": hashlib.sha256(data).hexdigest(),
        "manifest": policy,
        "criterion_evidence": evidence,
    }


def blockers(
    raw: object, *, root: Path | None, application_source: str, experiment_id: str, now: datetime
) -> list[str]:
    """Re-read accepted source and exact receipt bytes; never trust complete=true."""
    prefix = "wetting_incident_778_disposition:"
    if raw is None:
        return [prefix + "typed_evidence_missing"]
    if root is None:
        return [prefix + "source_root_missing"]
    try:
        expected = collect(root, application_source=application_source, experiment_id=experiment_id)
    except (OSError, ValueError, TypeError, KeyError):
        return [prefix + "source_evidence_invalid"]
    if raw != expected:
        return [prefix + "source_or_identity_mismatch"]
    missing = []
    firmware = set()
    for name, evidence in expected["criterion_evidence"].items():
        if evidence is None:
            missing.append(prefix + "missing:" + name)
            continue
        # Hash custody is not physical evidence interpretation. Do not remove
        # this denial until the actual native receipt contract has a reviewed
        # criterion-specific semantic validator. Nonempty bytes or a summary
        # marked qualified cannot establish measured physical acceptance.
        missing.append(prefix + "unsupported_measurement_contract:" + name)
        # These must be source-reviewed measurement receipts. The manifest pins
        # their bytes, and the current packet independently binds their lineage.
        keys = {
            "schema",
            "criterion",
            "result",
            "incident_issue",
            "device_id",
            "source_correction_commit",
            "application_source_revision",
            "experiment_id",
            "observed_at",
            "firmware_binary_sha256",
            "measurement_receipt",
        }
        try:
            if not isinstance(evidence, dict) or set(evidence) != keys:
                raise ValueError("shape")
            if (
                evidence["schema"],
                evidence["criterion"],
                evidence["result"],
                evidence["incident_issue"],
                evidence["device_id"],
                evidence["source_correction_commit"],
                evidence["application_source_revision"],
                evidence["experiment_id"],
            ) != (
                "verdify-wetting-criterion-qualification-v1",
                name,
                "qualified",
                778,
                DEVICE,
                CORRECTION,
                application_source,
                experiment_id,
            ):
                raise ValueError("identity")
            observed = datetime.fromisoformat(evidence["observed_at"].replace("Z", "+00:00"))
            if observed.tzinfo is None or not 0 <= (now.astimezone(UTC) - observed).total_seconds() <= 1800:
                raise ValueError("stale")
            digest = evidence["firmware_binary_sha256"]
            if not isinstance(digest, str) or SHA.fullmatch(digest) is None:
                raise ValueError("binary")
            # A second file is the retained actual measurement, not a status-only
            # qualification summary. Its hash and bounded native interpretation
            # must have been reviewed with the source manifest.
            measurement = _read(root, evidence["measurement_receipt"])
            if not measurement.strip():
                raise ValueError("measurement missing")
            firmware.add(digest)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            missing.append(prefix + "invalid:" + name)
    if len(firmware) > 1:
        missing.append(prefix + "firmware_binary_mismatch")
    return missing
