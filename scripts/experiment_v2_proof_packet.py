#!/usr/bin/env python3
"""Collect one source-stamped, non-actuating M8.1 proof-readiness packet.

The collector runs only in the attended direct-proof Job.  It has read-only
database credentials, read-only Kubernetes metadata/log access, and read-only
access to the backup PVC.  It never imports an ESPHome client, invokes a device
endpoint, changes a Kubernetes object, or writes the production database.

Gate P performs the three external/nonlocal preflights once.  Their complete
metadata-only receipts are cached in the Job's ``emptyDir`` and later physical
boundaries reuse the exact hashes.  Every boundary still recollects current
workloads, Argo state, backup state, writer/lease lineage, climate cycles,
experiment axes, component-grid attestation, and alerts.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import math
import os
import re
import ssl
import subprocess
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

INPUT_SCHEMA = "verdify-experiment-v2-readiness-input-v1"
STATE_SCHEMA = "verdify-experiment-v2-readiness-chain-v2"
PREFLIGHT_CACHE_SCHEMA = "verdify-experiment-v2-proof-preflight-cache-v1"
NAMESPACE = "verdify-prod"
ARGO_NAMESPACE = "argocd"
ARGO_APPLICATION = "verdify-prod-dark"
DEVICE_ID = "vallery/greenhouse-controller"
CORRECTED_ONE_OFF_PIN = "6b48dba7217438f5fdd7fb14fc8e067975cf1c35"
CORRECTED_ONE_OFF_JOB = "verdify-db-backup-verify-20260830t110155z"
QUALIFICATION = {
    "status": "degraded-pass",
    "source_kind": "ha_cycle_aligned_events",
    "evidence_class": "source_qualification",
    "window_started_at": "2026-08-30T10:18:36.414778Z",
    "window_ended_at": "2026-08-30T10:53:06.439259Z",
    "sample_count": 109,
    "minimum_contributors": 3,
    "receipt_sha256": "6b9af068b4c0bece0a7a0056d40bca7085ea02f9b92952ff40d9a356ab8c0ed4",
}
SCHEDULE_ACCEPTANCE_AFTER = datetime(2026, 8, 30, 8, 17, tzinfo=UTC)
CORE = {
    "verdify-api": ("Deployment", "api"),
    "verdify-db": ("StatefulSet", "db"),
    "verdify-hermes-iris": ("Deployment", "hermes-iris"),
    "verdify-ingestor": ("Deployment", "ingestor"),
    "verdify-mcp": ("Deployment", "mcp"),
}
APPLICATION_IMAGES = {
    "verdify-api": "api",
    "verdify-ingestor": "ingestor",
    "verdify-mcp": "mcp",
}
SURFACES = {
    "component_proof": "scripts/verify_component_proof_packet.py",
    "selector": "research/planner-efficacy/switchback/v2_selector.py",
    "executor_control": "ingestor/tasks/component_experiment.py",
    "locked_outcome": "research/planner-efficacy/switchback/v2_outcomes.py",
}
BOUNDARY_SEQUENCE = ("gate-p", "baseline-before", "aggressive", "baseline-after")
ZONES = ("north", "south", "east", "west")
METRICS = {"temp_f": "temp", "rh_pct": "rh", "vpd_kpa": "vpd"}
ATTESTATION = re.compile(
    r"^(?P<time>\S+) INFO component_entity_grid_attestation status=pass "
    r"grid_revision=(?P<grid>\S+) observation_receipt_sha256=(?P<receipt>[0-9a-f]{64}) "
    r"field_count=(?P<count>\d+) firmware_revision=(?P<firmware>\S+) "
    r"source_revision=(?P<source>[0-9a-f]{40}) connection_generation=(?P<connection>\d+)$",
    re.MULTILINE,
)
DESTABILIZER = re.compile(
    r"writer_lease: (?:renew error|SELF-FENCING|ACQUIRED)|"
    r"Writer lease LOST|reason=transport_reconnect|connection_generation_change|"
    r"component_entity_grid_attestation status=pass"
)
BACKUP_PATH = re.compile(r"/backups/(?P<name>verdify-(?P<stamp>\d{8}T\d{6}Z)\.dump)")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CollectionError(RuntimeError):
    """A fail-closed collection error that never includes a secret value."""


def zulu(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CollectionError("source timestamp is not timezone-aware")
    return parsed.astimezone(UTC)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def receipt(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def image_digest(value: str) -> str:
    marker = "@sha256:"
    if marker not in value:
        raise CollectionError("application image is not digest-pinned")
    digest = "sha256:" + value.rsplit(marker, 1)[1]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise CollectionError("application image carries a malformed digest")
    return digest


def ready(pod: Mapping[str, Any]) -> bool:
    return any(
        row.get("type") == "Ready" and row.get("status") == "True"
        for row in pod.get("status", {}).get("conditions", [])
    )


class KubeReader:
    """Minimal in-cluster, GET-only Kubernetes API client."""

    def __init__(self) -> None:
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "")
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        if not host:
            raise CollectionError("in-cluster Kubernetes service identity is absent")
        self.base = f"https://{host}:{port}"
        token_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
        ca_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
        if not token_path.is_file() or not ca_path.is_file():
            raise CollectionError("projected Kubernetes service-account identity is absent")
        self._token = token_path.read_text().strip()
        self._context = ssl.create_default_context(cafile=str(ca_path))

    def _get(self, path: str, *, accept: str = "application/json") -> bytes:
        request = urllib.request.Request(
            self.base + path,
            headers={"accept": accept, "authorization": f"Bearer {self._token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=20, context=self._context) as response:
                return response.read(16 * 1024 * 1024 + 1)
        except Exception as exc:  # noqa: BLE001 - redact the secret-bearing request
            raise CollectionError(f"Kubernetes GET failed for {path.split('?', 1)[0]}") from exc

    def json(self, path: str) -> dict[str, Any]:
        raw = self._get(path)
        if len(raw) > 16 * 1024 * 1024:
            raise CollectionError("Kubernetes response exceeded bounded size")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CollectionError("Kubernetes response was not JSON") from exc
        if not isinstance(value, dict):
            raise CollectionError("Kubernetes response root was not an object")
        return value

    def logs(self, pod: str) -> str:
        raw = self._get(
            f"/api/v1/namespaces/{NAMESPACE}/pods/{urllib.parse.quote(pod)}/log?container=ingestor&timestamps=false",
            accept="text/plain",
        )
        if len(raw) > 16 * 1024 * 1024:
            raise CollectionError("writer log response exceeded bounded size")
        return raw.decode("utf-8", errors="replace")


def _resource_path(kind: str, name: str) -> str:
    plural = {"Deployment": "deployments", "StatefulSet": "statefulsets"}[kind]
    return f"/apis/apps/v1/namespaces/{NAMESPACE}/{plural}/{name}"


def _list_path(group: str, plural: str, *, selector: str = "") -> str:
    prefix = f"/api/v1/namespaces/{NAMESPACE}" if group == "core" else f"/apis/{group}/namespaces/{NAMESPACE}"
    query = "" if not selector else "?labelSelector=" + urllib.parse.quote(selector)
    return f"{prefix}/{plural}{query}"


def _container(resource: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    rows = resource["spec"]["template"]["spec"]["containers"]
    try:
        return next(row for row in rows if row["name"] == name)
    except StopIteration as exc:
        raise CollectionError(f"workload container is absent: {name}") from exc


def _pod_container(pod: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    try:
        return next(row for row in pod.get("status", {}).get("containerStatuses", []) if row["name"] == name)
    except StopIteration as exc:
        raise CollectionError(f"running container status is absent: {name}") from exc


def collect_kube(
    kube: KubeReader,
    *,
    git_pin: str,
    application_source: str,
    experiment_id: str,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    observed_at = zulu(now)
    app = kube.json(f"/apis/argoproj.io/v1alpha1/namespaces/{ARGO_NAMESPACE}/applications/{ARGO_APPLICATION}")
    if app.get("status", {}).get("sync", {}).get("revision") != git_pin:
        raise CollectionError("Argo current revision differs from the expected Git pin")

    resources: dict[str, dict[str, Any]] = {}
    workloads: list[dict[str, Any]] = []
    for name, (kind, _container_name) in CORE.items():
        resource = kube.json(_resource_path(kind, name))
        resources[name] = resource
        desired = int(resource["spec"]["replicas"])
        status = resource.get("status", {})
        ready_count = int(status.get("readyReplicas", 0))
        available = int(status.get("availableReplicas", ready_count))
        healthy = (
            ready_count == desired
            and available == desired
            and int(status.get("observedGeneration", 0)) == int(resource["metadata"]["generation"])
        )
        workloads.append(
            {
                "name": name,
                "kind": kind,
                "ready": ready_count,
                "desired": desired,
                "healthy": healthy,
                "observed_at": observed_at,
            }
        )

    all_pods = kube.json(_list_path("core", "pods"))["items"]
    images: list[dict[str, str]] = []
    for workload, container_name in APPLICATION_IMAGES.items():
        desired_digest = image_digest(str(_container(resources[workload], container_name)["image"]))
        pods = [
            pod
            for pod in all_pods
            if pod.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") == CORE[workload][1]
            and pod.get("status", {}).get("phase") == "Running"
            and ready(pod)
            and not pod.get("metadata", {}).get("deletionTimestamp")
        ]
        if not pods:
            raise CollectionError(f"no current Ready pod for {workload}")
        running = {image_digest(str(_pod_container(pod, container_name)["imageID"])) for pod in pods}
        if running != {desired_digest}:
            raise CollectionError(f"desired/running image mismatch for {workload}")
        images.append(
            {
                "workload": workload,
                "rendered_digest": desired_digest,
                "running_digest": desired_digest,
                "application_source_revision": application_source,
            }
        )

    ingestor_pods = [
        pod
        for pod in all_pods
        if pod.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") == "ingestor"
        and pod.get("status", {}).get("phase") == "Running"
        and not pod.get("metadata", {}).get("deletionTimestamp")
    ]
    if len(ingestor_pods) != 1:
        raise CollectionError("current ingestor pod count is not one")
    writer_pod = ingestor_pods[0]
    writer_status = _pod_container(writer_pod, "ingestor")
    if not ready(writer_pod) or int(writer_status.get("restartCount", -1)) != 0:
        raise CollectionError("current writer pod is not Ready with zero restarts")
    lease = kube.json(f"/apis/coordination.k8s.io/v1/namespaces/{NAMESPACE}/leases/verdify-ingestor-writer")
    writer_name = writer_pod["metadata"]["name"]
    if lease.get("spec", {}).get("holderIdentity") != writer_name:
        raise CollectionError("writer pod does not match the current Lease holder")

    logs = kube.logs(writer_name)
    attestations = [match.groupdict() for match in ATTESTATION.finditer(logs)]
    if not attestations:
        raise CollectionError("current writer has no source-stamped component-grid attestation")
    attestation = attestations[-1]
    if int(attestation["count"]) != 48 or attestation["source"] != application_source:
        raise CollectionError("current component-grid attestation has wrong count/source")
    if int(attestation["connection"]) < 1:
        raise CollectionError("current component-grid attestation has invalid connection generation")
    ingestor_env = {
        row["name"]: row.get("value")
        for row in _container(resources["verdify-ingestor"], "ingestor").get("env", [])
        if "name" in row
    }
    if (
        ingestor_env.get("VERDIFY_POLICY_VECTOR_MODE"),
        ingestor_env.get("VERDIFY_COMPONENT_EXPERIMENT_ENABLED"),
        ingestor_env.get("VERDIFY_ACTIVE_EXPERIMENT_ID"),
    ) != ("off", "enabled", experiment_id):
        raise CollectionError("direct-proof ingestor capability is not the exact isolated configuration")

    stable_events: list[datetime] = [parse_time(writer_pod["status"]["startTime"])]
    for line in logs.splitlines():
        if DESTABILIZER.search(line):
            token = line.split(maxsplit=1)[0]
            try:
                stable_events.append(parse_time(token))
            except (CollectionError, ValueError):
                continue
    stable_since = max(stable_events)
    recurring = 0
    for line in logs.splitlines():
        if not DESTABILIZER.search(line):
            continue
        try:
            moment = parse_time(line.split(maxsplit=1)[0])
        except (CollectionError, ValueError):
            continue
        if moment > stable_since:
            recurring += 1

    app_status = app.get("status", {})
    operation_state = app_status.get("operationState", {})
    sync_result = operation_state.get("syncResult", {})
    # During the PostSync proof Job the current operation lives at the top
    # level.  After it succeeds Argo removes that field but retains the exact
    # operation under status.operationState.  Consult both so a completed
    # selective/pruning sync cannot masquerade as an ordinary full sync.
    operation = app.get("operation") or operation_state.get("operation") or {}
    sync = operation.get("sync") or {}
    resource_selector = sync.get("resources")
    prune = bool(sync.get("prune", False))
    return {
        "observed_at": observed_at,
        "workloads": workloads,
        "images": images,
        "writer_pod": writer_pod,
        "writer_name": writer_name,
        "writer_digest": next(row["running_digest"] for row in images if row["workload"] == "verdify-ingestor"),
        "writer_stable_since": zulu(stable_since),
        "writer_recurring_errors": recurring,
        "attestation": attestation,
        "argo": {
            "revision": app_status["sync"]["revision"],
            "source_path": app["spec"]["source"]["path"],
            "sync_status": app_status["sync"]["status"],
            "health_status": app_status["health"]["status"],
            "operation_phase": operation_state.get("phase", "Unknown"),
            "operation_revision": sync_result.get("revision") or sync.get("revision"),
            "prune": prune,
            "resource_selector": resource_selector,
            "observed_at": observed_at,
        },
        "all_pods": all_pods,
        "cronjob": kube.json(f"/apis/batch/v1/namespaces/{NAMESPACE}/cronjobs/verdify-db-backup"),
        "backup_jobs": kube.json(_list_path("batch/v1", "jobs", selector="app.kubernetes.io/component=db-backup"))[
            "items"
        ],
        "one_off_job": kube.json(f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs/{CORRECTED_ONE_OFF_JOB}"),
        "writer_fact": {
            "pod": writer_name,
            "pod_uid": writer_pod["metadata"]["uid"],
            "lease_uid": lease["metadata"]["uid"],
            "lease_renew_time": lease["spec"].get("renewTime"),
            "attestation_receipt_sha256": attestation["receipt"],
            "verified_at": observed_at,
        },
    }


async def _connect(prefix: str) -> asyncpg.Connection:
    user = os.environ.get(f"{prefix}_DB_USER", "")
    password = os.environ.get(f"{prefix}_DB_PASSWORD", "")
    if not user or not password:
        raise CollectionError(f"{prefix.lower()} read-only database identity is absent")
    try:
        connection = await asyncpg.connect(
            host=os.environ.get("DB_HOST", "verdify-db"),
            port=int(os.environ.get("DB_PORT", "5432")),
            database=os.environ.get("DB_NAME", "verdify"),
            user=user,
            password=password,
            command_timeout=30,
            server_settings={"default_transaction_read_only": "on", "statement_timeout": "30000"},
        )
    except Exception as exc:  # noqa: BLE001 - never expose driver DSN/credential text
        raise CollectionError(f"{prefix.lower()} read-only database connection failed") from exc
    return connection


async def collect_db(experiment_id: str) -> dict[str, Any]:
    connection = await _connect("READ")
    try:
        status = await connection.fetchrow("SELECT * FROM public.fn_experiment_v2_api_status($1::uuid)", experiment_id)
        runtime = await connection.fetchrow(
            "SELECT * FROM public.fn_experiment_v2_executor_runtime($1::uuid,$2::text)",
            experiment_id,
            DEVICE_ID,
        )
        generation = await connection.fetchrow(
            """
            SELECT runtime_instance_id, writer_generation, connection_generation,
                   restart_detected, reconnect_detected, recorded_at
              FROM public.experiment_v2_runtime_generations
             WHERE experiment_id=$1::uuid AND device_id=$2::text
             ORDER BY generation_event_id DESC LIMIT 1
            """,
            experiment_id,
            DEVICE_ID,
        )
        climate = await connection.fetch(
            """
            SELECT c.ts, c.temp_avg, c.temp_north, c.temp_south, c.temp_east, c.temp_west,
                   c.rh_avg, c.rh_north, c.rh_south, c.rh_east, c.rh_west,
                   c.vpd_avg, c.vpd_north, c.vpd_south, c.vpd_east, c.vpd_west,
                   d.active_probe_count, d.probe_health
              FROM (SELECT * FROM public.climate ORDER BY ts DESC LIMIT 2) c
              LEFT JOIN LATERAL (
                SELECT active_probe_count, probe_health FROM public.diagnostics
                 WHERE ts <= c.ts ORDER BY ts DESC LIMIT 1
              ) d ON true
             ORDER BY c.ts
            """
        )
        bands = await connection.fetch(
            "SELECT * FROM public.fn_band_setpoint_provenance(clock_timestamp(),'vallery') ORDER BY parameter"
        )
        targets = await connection.fetchrow(
            """
            SELECT c.ts, c.house_temp_target_f, c.house_vpd_target,
                   b.temp_target AS served_temp_target,
                   b.vpd_target AS served_vpd_target
              FROM public.climate c
              CROSS JOIN LATERAL public.fn_band_setpoints(c.ts) b
             WHERE c.house_temp_target_f IS NOT NULL AND c.house_vpd_target IS NOT NULL
             ORDER BY c.ts DESC LIMIT 1
            """
        )
        open_alerts = await connection.fetch(
            """
            SELECT id, ts, alert_type, severity, category, sensor_id, message,
                   disposition, updated_at
              FROM public.v_open_alerts ORDER BY ts, id
            """
        )
    finally:
        await connection.close()
    if status is None or runtime is None or generation is None or len(climate) != 2 or targets is None:
        raise CollectionError("database readiness projection is incomplete")
    return {
        "status": dict(status),
        "runtime": dict(runtime),
        "generation": dict(generation),
        "climate": [dict(row) for row in climate],
        "bands": [dict(row) for row in bands],
        "targets": dict(targets),
        "open_alerts": [dict(row) for row in open_alerts],
    }


def event_id(field: str, observed_at: str, value: object) -> str:
    return "db-" + hashlib.sha256(canonical_bytes([field, observed_at, value])).hexdigest()[:32]


def climate_samples(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for row in rows:
        observed_at = zulu(row["ts"])
        cycle_id = "db-climate-" + observed_at.replace(":", "").replace("-", "")

        def cell(field: str) -> dict[str, object]:
            value = row.get(field)
            return {
                "value": value,
                "observed_at": observed_at,
                "source_event_id": event_id(field, observed_at, value),
                "source_cycle_id": cycle_id,
            }

        zones: dict[str, Any] = {}
        contributors: list[str] = []
        for zone in ZONES:
            triplet = {output: cell(f"{source}_{zone}") for output, source in METRICS.items()}
            zones[zone] = triplet
            values = [triplet[metric]["value"] for metric in METRICS]
            if all(
                isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                for value in values
            ):
                contributors.append(zone)
        samples.append(
            {
                "cycle_id": cycle_id,
                "sample_at": observed_at,
                "declared_contributors": contributors,
                "zones": zones,
                "aggregates": {output: cell(f"{source}_avg") for output, source in METRICS.items()},
                "diagnostics": {
                    "active_probe_count": int(row.get("active_probe_count") or 0),
                    "probe_health": str(row.get("probe_health") or "unknown"),
                },
            }
        )
    return samples


def passive_424(db: Mapping[str, Any], *, observed_at: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the six-series passive receipt with exact numeric equality."""

    rows = {str(row["parameter"]): row for row in db["bands"]}
    targets = db["targets"]
    series: list[dict[str, Any]] = []
    for name in ("temp_low", "temp_high", "vpd_low", "vpd_high"):
        row = rows.get(name)
        if row is None:
            series.append({"series": name, "status": "unobservable"})
            continue
        series.append(
            {
                "series": name,
                "served": row["dispatcher_value"],
                "control": row["firmware_setpoint_value"],
                "observed": row["cfg_readback_value"],
                "served_at": zulu(row["ts"]),
                "control_at": None if row["firmware_setpoint_ts"] is None else zulu(row["firmware_setpoint_ts"]),
                "observed_at": None if row["cfg_readback_ts"] is None else zulu(row["cfg_readback_ts"]),
                "status": "present",
            }
        )
    for name, served_key, device_key in (
        ("temp_target", "served_temp_target", "house_temp_target_f"),
        ("vpd_target", "served_vpd_target", "house_vpd_target"),
    ):
        series.append(
            {
                "series": name,
                "served": targets[served_key],
                # These are firmware-computed publishes (migration 182), not a
                # desired row or inferred cfg route.  The one passive publish is
                # both the controller's reported effective target and its raw
                # observed telemetry value; source semantics stay explicit.
                "control": targets[device_key],
                "observed": targets[device_key],
                "served_at": zulu(targets["ts"]),
                "control_at": zulu(targets["ts"]),
                "observed_at": zulu(targets["ts"]),
                "status": "present",
            }
        )
    agreement = True
    for row in series:
        values = (row.get("served"), row.get("control"), row.get("observed"))
        finite = all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in values
        )
        row["agreement"] = finite and float(values[0]) == float(values[1]) == float(values[2])
        row["status"] = "resolved" if row["agreement"] else row["status"]
        agreement = agreement and bool(row["agreement"])
    raw_receipt = {
        "schema": "verdify-experiment-v2-passive-424-v1",
        "observed_at": observed_at,
        "passive": True,
        "device_call_count": 0,
        "series": series,
    }
    evidence = {
        "status": "pass" if agreement else "fail",
        "observed_at": observed_at,
        "receipt_sha256": receipt(raw_receipt),
        "passive": True,
        "agreement": agreement,
        "series_checked": len(series),
        "device_call_count": 0,
    }
    return evidence, raw_receipt


def _backup_artifact(job: Mapping[str, Any], kube: KubeReader) -> dict[str, Any]:
    name = job["metadata"]["name"]
    pods = kube.json(_list_path("core", "pods", selector=f"batch.kubernetes.io/job-name={name}"))["items"]
    succeeded = [pod for pod in pods if pod.get("status", {}).get("phase") == "Succeeded"]
    if not succeeded:
        raise CollectionError(f"backup job does not have a retained successful pod: {name}")
    pod = sorted(succeeded, key=lambda row: row["metadata"]["creationTimestamp"])[-1]
    raw = kube._get(
        f"/api/v1/namespaces/{NAMESPACE}/pods/{pod['metadata']['name']}/log?container=pg-dump",
        accept="text/plain",
    ).decode("utf-8", errors="replace")
    matches = list(BACKUP_PATH.finditer(raw))
    artifact = matches[-1]["name"] if matches else ""
    path = Path("/backups") / artifact
    complete = any(
        row.get("type") == "Complete" and row.get("status") == "True"
        for row in job.get("status", {}).get("conditions", [])
    )
    if path.is_file():
        with path.open("rb") as handle:
            header = handle.read(5)
    else:
        header = b""
    partial = Path(str(path) + ".partial").exists() if artifact else True
    return {
        "job_uid": job["metadata"]["uid"],
        "pod_uid": pod["metadata"]["uid"],
        "completed_at": job.get("status", {}).get("completionTime") or job["metadata"]["creationTimestamp"],
        "artifact": artifact,
        "artifact_bytes": path.stat().st_size if path.is_file() else 0,
        "restorable": complete and header == b"PGDMP",
        "partial_artifact": partial,
    }


def backup_evidence(kube: KubeReader, facts: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    one_off = _backup_artifact(facts["one_off_job"], kube)
    controller_jobs = []
    cron_uid = facts["cronjob"]["metadata"]["uid"]
    for job in facts["backup_jobs"]:
        annotations = job.get("metadata", {}).get("annotations", {})
        owner = job.get("metadata", {}).get("ownerReferences", [])
        scheduled = annotations.get("batch.kubernetes.io/cronjob-scheduled-timestamp")
        if not scheduled or parse_time(scheduled) <= SCHEDULE_ACCEPTANCE_AFTER:
            continue
        if not any(row.get("uid") == cron_uid and row.get("controller") for row in owner):
            continue
        controller_jobs.append(job)
    controller_jobs.sort(
        key=lambda row: row["metadata"]["annotations"]["batch.kubernetes.io/cronjob-scheduled-timestamp"]
    )
    successful = [
        row
        for row in controller_jobs
        if any(
            condition.get("type") == "Complete" and condition.get("status") == "True"
            for condition in row.get("status", {}).get("conditions", [])
        )
    ]
    if successful:
        controller = _backup_artifact(successful[-1], kube)
        controller_status = "succeeded"
    else:
        latest = controller_jobs[-1] if controller_jobs else facts["one_off_job"]
        controller = {
            "job_uid": latest["metadata"]["uid"],
            "completed_at": latest.get("status", {}).get("completionTime")
            or latest.get("metadata", {}).get("creationTimestamp"),
            "artifact_bytes": 0,
            "restorable": False,
            "partial_artifact": True,
        }
        controller_status = "failed"
    full_acceptance = bool(successful)

    def guarded(row: Mapping[str, Any], *, status: str, source_pin: str) -> dict[str, Any]:
        return {
            "status": status,
            "completed_at": zulu(parse_time(str(row["completed_at"]))),
            "artifact_bytes": int(row["artifact_bytes"]),
            "restorable": bool(row["restorable"]),
            "partial_artifact": bool(row["partial_artifact"]),
            "source_git_pin": source_pin,
            "receipt_sha256": receipt(row),
        }

    return (
        {
            "corrected_one_off": guarded(one_off, status="succeeded", source_pin=CORRECTED_ONE_OFF_PIN),
            "controller_owned": guarded(
                controller,
                status=controller_status,
                source_pin=CORRECTED_ONE_OFF_PIN,
            ),
            "policy_max_age_seconds": 93600,
        },
        full_acceptance,
    )


def _run_metadata(command: Sequence[str], *, timeout: int) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)
    if completed.returncode != 0:
        raise CollectionError("non-actuating preflight failed closed")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CollectionError("non-actuating preflight emitted no metadata receipt") from exc
    if not isinstance(value, dict) or value.get("status") != "pass":
        raise CollectionError("non-actuating preflight did not pass")
    return value


def gate_p_preflights(
    *,
    kube: KubeReader,
    db: Mapping[str, Any],
    application_source: str,
    experiment_id: str,
    cache: Path,
) -> dict[str, Any]:
    observed_at = zulu(datetime.now(UTC))
    mcp_pods = [
        pod
        for pod in kube.json(_list_path("core", "pods", selector="app.kubernetes.io/component=mcp"))["items"]
        if pod.get("status", {}).get("phase") == "Running" and ready(pod)
    ]
    command = [
        "python",
        "/app/scripts/mcp-security-acceptance.py",
        "--repeats",
        "1",
        "--expected-tools-config",
        "/app/readiness-source/deploy/k8s/components/hermes-iris/hermes-config.yaml",
    ]
    command.extend(("--endpoint", "service=http://verdify-mcp:8000/mcp"))
    command.extend(("--readiness-url", "service=http://verdify-mcp:8000/readyz"))
    for pod in mcp_pods:
        label = pod["metadata"]["name"]
        ip = pod["status"]["podIP"]
        command.extend(("--endpoint", f"{label}=http://{ip}:8000/mcp"))
        command.extend(("--readiness-url", f"{label}=http://{ip}:8000/readyz"))
    auth_raw = _run_metadata(command, timeout=180)
    auth = {
        "status": "pass",
        "observed_at": observed_at,
        "application_source_revision": application_source,
        "experiment_id": experiment_id,
        "receipt_sha256": receipt(auth_raw),
        "replica_count": len(mcp_pods),
        "replicas_checked": len(mcp_pods),
        "public_unauthenticated_denied": True,
        "unknown_bearer_denied": True,
        "authenticated_iris_passed": True,
        "admin_query_denied": True,
        "session_identifier_absent": bool(auth_raw.get("stateless")),
    }

    provider_path = Path("/tmp/experiment-v2-provider-preflight.json")  # noqa: S108
    provider_summary = _run_metadata(
        [
            "python",
            "-m",
            "experiment_orchestrator.preflight",
            "--identity",
            "/app/readiness-source/research/planner-efficacy/protocols/shadow-v2/selector-artifact-v1.json",
            "--receipt",
            str(provider_path),
        ],
        timeout=180,
    )
    provider_bytes = provider_path.read_bytes()
    if not provider_bytes.endswith(b"\n") or provider_bytes.endswith(b"\n\n"):
        raise CollectionError("provider preflight receipt framing is invalid")
    provider_payload = provider_bytes[:-1]
    provider_receipt = hashlib.sha256(provider_payload).hexdigest()
    if provider_summary.get("receipt_sha256") != provider_receipt:
        raise CollectionError("provider preflight summary/receipt identity differs")
    provider_raw = json.loads(provider_payload)
    provider = {
        "status": "pass",
        "observed_at": observed_at,
        "application_source_revision": application_source,
        "experiment_id": experiment_id,
        "receipt_sha256": provider_receipt,
        "non_actuating": True,
        "credential_present": bool(os.environ.get("VERDIFY_EXPERIMENT_SELECTOR_API_KEY")),
        "provider_reachable": provider_raw.get("status") == "pass",
        "request_count": max(1, len(provider_raw.get("attempt_receipt_sha256", []))),
        "device_call_count": 0,
    }

    passive, passive_raw = passive_424(db, observed_at=observed_at)
    passive.update(
        {
            "application_source_revision": application_source,
            "experiment_id": experiment_id,
        }
    )
    payload = {
        "schema": PREFLIGHT_CACHE_SCHEMA,
        "authentication_686": auth,
        "provider_preflight": provider,
        "served_control_observed_424": passive,
        "supporting": {
            "authentication_686": auth_raw,
            "provider_preflight": provider_raw,
            "served_control_observed_424": passive_raw,
        },
    }
    cache.write_text(json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n")
    return payload


def cached_preflights(cache: Path) -> dict[str, Any]:
    try:
        value = json.loads(cache.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectionError("Gate P preflight cache is absent or malformed") from exc
    if value.get("schema") != PREFLIGHT_CACHE_SCHEMA:
        raise CollectionError("Gate P preflight cache has the wrong schema")
    for label in ("authentication_686", "provider_preflight", "served_control_observed_424"):
        if not SHA256.fullmatch(str(value.get(label, {}).get("receipt_sha256", ""))):
            raise CollectionError(f"Gate P preflight cache receipt is invalid: {label}")
    return value


def chain(boundary: str, state_path: Path) -> dict[str, Any]:
    sequence = BOUNDARY_SEQUENCE.index(boundary)
    if sequence == 0:
        if state_path.exists():
            raise CollectionError("Gate P cannot start with an existing readiness chain")
        return {"attempt_id": str(uuid.uuid4()), "sequence": 0, "previous_receipt_sha256": None}
    try:
        state = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectionError("noninitial proof boundary has no valid readiness chain") from exc
    if state.get("schema") != STATE_SCHEMA or state.get("next_sequence") != sequence:
        raise CollectionError("readiness chain sequence does not match the requested boundary")
    return {
        "attempt_id": state["attempt_id"],
        "sequence": sequence,
        "previous_receipt_sha256": state["last_receipt_sha256"],
    }


def source_dependencies(template: Mapping[str, Any], root: Path, *, application_source: str) -> dict[str, Any]:
    dependencies = copy.deepcopy(template["dependencies"])
    dependencies["application_source_revision"] = application_source
    rows = {row["name"]: row for row in dependencies["surfaces"]}
    for name, relative in SURFACES.items():
        path = root / relative
        if not path.is_file():
            raise CollectionError(f"source dependency is absent: {name}")
        # The fixture is the reviewed dependency declaration, not a template
        # for silently blessing whatever entered the image.  A causal source
        # change must update that declaration deliberately or collection stops.
        if hashlib.sha256(path.read_bytes()).hexdigest() != rows[name]["source_sha256"]:
            raise CollectionError(f"source dependency differs from its reviewed declaration: {name}")
        rows[name]["application_source_revision"] = application_source
    return dependencies


def alert_projection(db: Mapping[str, Any], *, observed_at: str) -> list[dict[str, Any]]:
    open_rows = db["open_alerts"]
    south = [
        row
        for row in open_rows
        if str(row.get("sensor_id") or "").startswith("climate.") and str(row.get("sensor_id") or "").endswith("_south")
    ]
    hydro = [row for row in open_rows if str(row.get("sensor_id") or "").startswith("climate.hydro_")]
    required_south = {"climate.temp_south", "climate.rh_south", "climate.vpd_south"}
    if not required_south.issubset({str(row.get("sensor_id")) for row in south}) or not hydro:
        raise CollectionError("required south/hydro degradation alerts are not visible")
    projection = [
        {
            "alert_id": "south-open-" + receipt(sorted(str(row["id"]) for row in south))[:16],
            "alert_type": "sensor_offline",
            "scope": "south_wall_probe",
            "disposition": "acknowledged",
            "observed_at": observed_at,
            "classification": "accepted_nonblocking_degradation",
            "causal": False,
            "decision_issue_url": "https://github.com/VerdifyConsultancy/verdify-platform/issues/748",
            "maintenance_issue_url": "https://github.com/VerdifyConsultancy/verdify-platform/issues/751",
        },
        {
            "alert_id": "hydro-open-" + receipt(sorted(str(row["id"]) for row in hydro))[:16],
            "alert_type": "sensor_offline",
            "scope": "hydroponic_monitor",
            "disposition": "open",
            "observed_at": observed_at,
            "classification": "accepted_nonblocking_degradation",
            "causal": False,
            "decision_issue_url": "https://github.com/VerdifyConsultancy/verdify-platform/issues/748",
            "maintenance_issue_url": "https://github.com/VerdifyConsultancy/verdify-platform/issues/751",
        },
    ]
    accepted_ids = {row["id"] for row in south + hydro}
    for row in open_rows:
        if row["id"] in accepted_ids:
            continue
        alert_type = str(row.get("alert_type") or "unknown")
        sensor_id = str(row.get("sensor_id") or row["id"])
        # Unknown open alerts retain safe metadata but no free-form message.
        # Until an explicit source-grounded classification exists, conservatively
        # treat each as causal and let the guard block physical work.
        projection.append(
            {
                "alert_id": str(row["id"]),
                "alert_type": alert_type,
                "scope": f"open_alert:{alert_type}:{sensor_id}",
                "disposition": str(row.get("disposition") or "open"),
                "observed_at": observed_at,
                "classification": "unclassified",
                "causal": True,
                "decision_issue_url": "",
                "maintenance_issue_url": "",
            }
        )
    return projection


def fresh_gate_p_authorization(now: datetime) -> bool:
    reference = os.environ.get("VERDIFY_DIRECT_PROOF_AUTHORIZATION_REF", "")
    try:
        authorized_from = parse_time(os.environ["VERDIFY_DIRECT_PROOF_AUTHORIZED_FROM"])
        authorized_to = parse_time(os.environ["VERDIFY_DIRECT_PROOF_AUTHORIZED_TO"])
    except (KeyError, CollectionError, ValueError):
        return False
    duration = (authorized_to - authorized_from).total_seconds()
    return (
        bool(reference)
        and reference != "REPLACE_BEFORE_ACTIVATION"
        and authorized_from <= now < authorized_to
        and 3 * 60 <= duration <= 12 * 60 * 60
    )


def assemble(
    template: dict[str, Any],
    *,
    boundary: str,
    git_pin: str,
    application_source: str,
    experiment_id: str,
    state_path: Path,
    repo_root: Path,
    kube: KubeReader,
    kube_facts: Mapping[str, Any],
    db: Mapping[str, Any],
    preflights: Mapping[str, Any],
) -> dict[str, Any]:
    packet = copy.deepcopy(template)
    now = zulu(datetime.now(UTC))
    status = db["status"]
    runtime = db["runtime"]
    generation = db["generation"]
    if runtime.get("writer_generation") is None or runtime.get("connection_generation") is None:
        # Gate P may run before the component worker has observed the just-rolled
        # source.  Do not borrow an old generation: fail collection until the
        # current component-enabled writer registers its exact runtime lineage.
        raise CollectionError("current component runtime generation is not registered")
    if int(generation["connection_generation"]) != int(kube_facts["attestation"]["connection"]):
        raise CollectionError("database/current transport connection generations differ")
    lease_generation = int(status["lease_generation"])
    writer_generation = int(runtime["writer_generation"])
    connection_generation = int(runtime["connection_generation"])
    registry_revision = str(status["registry_revision"])
    backup, full_acceptance = backup_evidence(kube, kube_facts)
    writer_fact = dict(kube_facts["writer_fact"])
    writer_fact.update(
        {
            "lease_generation": lease_generation,
            "writer_generation": writer_generation,
            "connection_generation": connection_generation,
        }
    )
    component = {
        "status": "pass",
        "observed_at": now,
        "application_source_revision": application_source,
        "experiment_id": experiment_id,
        "receipt_sha256": kube_facts["attestation"]["receipt"],
        "expected_components": 48,
        "observed_components": 48,
        "fresh": True,
        "registry_revision": registry_revision,
        "lease_generation": lease_generation,
        "writer_generation": writer_generation,
        "connection_generation": connection_generation,
    }
    writer = {
        "status": "pass",
        "observed_at": now,
        "application_source_revision": application_source,
        "experiment_id": experiment_id,
        "receipt_sha256": receipt(writer_fact),
        "current_writer_count": 1,
        "lease_holder_matches": True,
        "generation_stable": (datetime.now(UTC) - parse_time(kube_facts["writer_stable_since"])).total_seconds()
        >= 1800,
        "component_truth_48_of_48": True,
        "lease_generation": lease_generation,
        "writer_generation": writer_generation,
        "connection_generation": connection_generation,
    }
    packet.update(
        {
            "schema": INPUT_SCHEMA,
            "mode": "proof",
            "boundary": boundary,
            "packet_id": str(uuid.uuid4()),
            "captured_at": now,
            "guard": chain(boundary, state_path),
            "provenance": {
                "git_pin": git_pin,
                "application_source_revision": application_source,
                "rendered_git_pin": git_pin,
                "running_git_pin": git_pin,
                "experiment_id": experiment_id,
                "registry_revision": registry_revision,
                "images": kube_facts["images"],
                "writer": {
                    "lease_holder": kube_facts["writer_name"],
                    "current_writer_count": 1,
                    "lease_generation": lease_generation,
                    "writer_generation": writer_generation,
                    "connection_generation": connection_generation,
                    "stable_since": kube_facts["writer_stable_since"],
                    "observed_at": now,
                    "application_source_revision": application_source,
                    "running_digest": kube_facts["writer_digest"],
                    "recurring_error_count": kube_facts["writer_recurring_errors"],
                },
            },
            "workloads": kube_facts["workloads"],
            "runtime": {
                "experiment_feature_mode": "off",
                "active_experiment_id": experiment_id,
                "policy_vector_mode": "off",
                "component_enabled": bool(status["component_enabled"]),
                "admission_state": str(status["admission_state"]),
                "open_exposure_count": int(status["open_exposure_count"]),
                "experiment_id": experiment_id,
                "lease_generation": lease_generation,
                "writer_generation": writer_generation,
                "connection_generation": connection_generation,
                "registry_revision": registry_revision,
            },
            "backup": backup,
            "argo": kube_facts["argo"],
            "climate": {
                "max_source_age_seconds": 90,
                "qualification_capture": {**QUALIFICATION, "application_source_revision": application_source},
                "samples": climate_samples(db["climate"]),
            },
            "alerts": alert_projection(db, observed_at=now),
            "dependencies": source_dependencies(template, repo_root, application_source=application_source),
            "evidence": {
                "component_grid": component,
                "authentication_686": preflights["authentication_686"],
                "provider_preflight": preflights["provider_preflight"],
                "served_control_observed_424": preflights["served_control_observed_424"],
                "writer_433": writer,
            },
            "issue_state": {
                "decision_748": {
                    "accepted": True,
                    "issue_url": "https://github.com/VerdifyConsultancy/verdify-platform/issues/748",
                },
                "maintenance_751": {
                    "deferred": True,
                    "issue_url": "https://github.com/VerdifyConsultancy/verdify-platform/issues/751",
                },
                "recovery_747": {
                    "corrected_one_off_complete": True,
                    "full_acceptance_complete": full_acceptance,
                    "issue_url": "https://github.com/VerdifyConsultancy/verdify-platform/issues/747",
                },
                "gate_p_641": {
                    "issue_url": "https://github.com/VerdifyConsultancy/verdify-platform/issues/641",
                    "prerequisites": [
                        {"name": name, "complete": complete}
                        for name, complete in (
                            ("boundary_observation_contract", True),
                            ("climate_quorum", True),
                            ("component_grid_48_of_48", True),
                            ("controller_backup_current", full_acceptance),
                            ("degradation_classified", True),
                            ("exact_source_and_images_pinned", True),
                            ("fresh_gate_p_authorization", fresh_gate_p_authorization(datetime.now(UTC))),
                            ("recovery_path_ready", True),
                            ("stable_writer_lease_generation", writer["generation_stable"]),
                            ("zero_exposure", int(status["open_exposure_count"]) == 0),
                        )
                    ],
                },
            },
        }
    )
    return packet


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--boundary", choices=BOUNDARY_SEQUENCE, required=True)
    result.add_argument("--expected-git-pin", required=True)
    result.add_argument("--expected-application-source", required=True)
    result.add_argument("--experiment-id", required=True)
    result.add_argument("--state", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument(
        "--template",
        type=Path,
        default=Path("/app/readiness-source/tests/fixtures/experiment-v2-readiness/base-proof.json"),
    )
    result.add_argument("--repo-root", type=Path, default=Path("/app/readiness-source"))
    result.add_argument(
        "--preflight-cache",
        type=Path,
        default=Path("/tmp/experiment-v2-proof-preflight-cache.json"),  # noqa: S108
    )
    return result


async def async_main(args: argparse.Namespace) -> int:
    try:
        template = json.loads(args.template.read_text())
        kube = KubeReader()
        kube_facts = collect_kube(
            kube,
            git_pin=args.expected_git_pin,
            application_source=args.expected_application_source,
            experiment_id=args.experiment_id,
        )
        db = await collect_db(args.experiment_id)
        preflights = (
            gate_p_preflights(
                kube=kube,
                db=db,
                application_source=args.expected_application_source,
                experiment_id=args.experiment_id,
                cache=args.preflight_cache,
            )
            if args.boundary == "gate-p"
            else cached_preflights(args.preflight_cache)
        )
        packet = assemble(
            template,
            boundary=args.boundary,
            git_pin=args.expected_git_pin,
            application_source=args.expected_application_source,
            experiment_id=args.experiment_id,
            state_path=args.state,
            repo_root=args.repo_root,
            kube=kube,
            kube_facts=kube_facts,
            db=db,
            preflights=preflights,
        )
        args.output.write_text(json.dumps(packet, allow_nan=False, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"packet_sha256": receipt(packet), "status": "collected"}, sort_keys=True))
        return 0
    except (CollectionError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        print(json.dumps({"status": "failed-closed"}, sort_keys=True))
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
