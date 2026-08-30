"""Audited API-only commands used by dormant M8 GitOps components.

There is no database client or device client in this module.  Kubernetes Jobs
invoke exactly one lifecycle action through the authenticated component API;
the manifests are not referenced by a production overlay until an operator
deliberately selects the matching stage.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from .contracts import ContractError, canonical_json_bytes
from .launch_artifacts import TIMEZONE, DirectLaunchDesign, parse_direct_launch_design


def _api_root(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "verdify-api"
        or parsed.port != 8000
        or parsed.path.rstrip("/")
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ContractError("launch control API must be exact in-cluster http://verdify-api:8000")
    return "http://verdify-api:8000"


def _state_precondition(status: dict[str, object]) -> dict[str, object]:
    return {
        "expected_admission_state": status["admission_state"],
        "expected_component_enabled": status["db_component_enabled"],
        "expected_execution_phase": status["execution_phase"],
        "expected_lease_generation": status["lease_generation"],
        "expected_lifecycle_status": status["lifecycle_status"],
        "expected_revision_bundle_sha256": status["revision_bundle_sha256"],
    }


async def _request(
    client: httpx.AsyncClient,
    *,
    method: str,
    url: str,
    token: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    response = await client.request(
        method,
        url,
        headers={"X-Verdify-Experiment-Token": token, "accept": "application/json"},
        json=payload,
    )
    if response.status_code != 200:
        raise ContractError(f"audited lifecycle API rejected {method} with status {response.status_code}")
    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise ContractError("audited lifecycle API returned a non-JSON receipt")
    try:
        result = response.json()
    except json.JSONDecodeError as exc:
        raise ContractError("audited lifecycle API receipt is malformed") from exc
    if not isinstance(result, dict):
        raise ContractError("audited lifecycle API receipt must be an object")
    return result


async def execute_control(
    *,
    action: str,
    api_root: str,
    token: str,
    audit_ref: str,
    design: DirectLaunchDesign | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bytes:
    if not token or any(character.isspace() for character in token):
        raise ContractError("experiment API credential is unavailable or malformed")
    root = _api_root(api_root)
    if action not in {"lock-design", "approve-day1", "emergency-hold"}:
        raise ContractError("launch control action is unknown")
    experiment_id = design.experiment_id if design is not None else os.environ.get("VERDIFY_ACTIVE_EXPERIMENT_ID", "")
    if not experiment_id:
        raise ContractError("launch control experiment identity is unavailable")
    base = f"{root}/api/v1/experiments/{experiment_id}"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0),
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    ) as client:
        status = await _request(
            client,
            method="GET",
            url=f"{base}/component-status",
            token=token,
        )
        if action != "emergency-hold" and status.get("open_exposures") != 0:
            raise ContractError("launch control requires an explicit zero-open-exposure status")
        command: dict[str, object] = {
            "audit_ref": audit_ref,
            **_state_precondition(status),
        }
        if action == "lock-design":
            if design is None:
                raise ContractError("lock-design requires one canonical design")
            if (
                status.get("lifecycle_status"),
                status.get("execution_phase"),
                status.get("admission_state"),
                status.get("db_component_enabled"),
            ) != ("draft", "shadow", "closed", False):
                raise ContractError("design lock requires the exact feature-off draft state")
            command.update({"action": "direct_launch_commit", **design.api_lock_fields()})
        elif action == "approve-day1":
            if (
                status.get("lifecycle_status"),
                status.get("execution_phase"),
                status.get("admission_state"),
                status.get("db_component_enabled"),
            ) != ("armed", "randomized", "closed", True):
                raise ContractError("day-1 approval requires finalized armed closed state")
            approvals = status.get("approvals")
            if not isinstance(approvals, dict) or approvals.get("randomized_day_1") is not False:
                raise ContractError("day-1 approval must be absent before the separate command")
            command["action"] = "direct_launch_approve_day1"
        else:
            if status.get("admission_state") == "emergency_hold":
                return canonical_json_bytes(
                    {
                        "action": "emergency-hold",
                        "audit_ref": audit_ref,
                        "experiment_id": experiment_id,
                        "idempotent": True,
                        "status": "already_held",
                    },
                    reject_forbidden_fields=False,
                )
            command.update(
                {
                    "action": "set_admission",
                    "reason": "exposure-close-first GitOps rollback; facility authority yielded",
                    "target_admission_state": "emergency_hold",
                }
            )
        result = await _request(
            client,
            method="POST",
            url=f"{base}/component-control/commands",
            token=token,
            payload=command,
        )
    state = result.get("state")
    if not isinstance(state, dict):
        raise ContractError("lifecycle command receipt omitted the resulting state")
    return canonical_json_bytes(
        {
            "action": action,
            "audit_ref": audit_ref,
            "experiment_id": experiment_id,
            "receipt_sha256": hashlib.sha256(canonical_json_bytes(result)).hexdigest(),
            "resulting_admission_state": state.get("admission_state"),
            "resulting_execution_phase": state.get("execution_phase"),
            "resulting_lifecycle_status": state.get("lifecycle_status"),
            "status": "pass",
        },
        reject_forbidden_fields=False,
    )


async def _main(args: argparse.Namespace) -> int:
    design = None
    if args.design is not None:
        design = parse_direct_launch_design(
            args.design.read_bytes(),
            now_local_date=datetime.now(ZoneInfo(TIMEZONE)).date(),
        )
    raw = await execute_control(
        action=args.action,
        api_root=os.environ.get("VERDIFY_EXPERIMENT_API_BASE_URL", ""),
        token=os.environ.get("VERDIFY_EXPERIMENT_API_TOKEN", ""),
        audit_ref=args.audit_ref,
        design=design,
    )
    args.receipt.write_bytes(raw + b"\n")
    print(json.dumps({"receipt_sha256": hashlib.sha256(raw).hexdigest(), "status": "pass"}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one audited experiment-v2 lifecycle command")
    parser.add_argument("action", choices=("lock-design", "approve-day1", "emergency-hold"))
    parser.add_argument("--audit-ref", required=True)
    parser.add_argument("--design", type=Path)
    parser.add_argument("--receipt", type=Path, default=Path("/dev/termination-log"))
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
