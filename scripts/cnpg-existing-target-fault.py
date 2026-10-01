"""Existing isolated CNPG primary-Pod loss qualification; ROOT executes only.

Default render writes local manifests/ticket. Explicit run uses an existing
UID-bound client, writes only rehearsal_bootstrap sentinels, and issues exactly
one ordinary UID-precondition primary Pod DELETE. Never creates/deletes a
namespace, Cluster, PVC/PV, credential, or product service. No host-loss claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

NS = "verdify-db-rehearsal"
CLUSTER = "verdify-cnpg-rehearsal"
UID = "e11f1014-a77e-4ccf-9d97-e8cf5c037484"
IMAGE = (
    "registry.vallery.net/verdifyconsultancy/verdify-timescaledb-cnpg:16.13-ts2.25.2@sha256:"
    "8b461e37d18aa049704eb6a9cde2ba9af0f955f8d3725e921070d2450bb2f137"
)
HOST = "topology.vallery.net/proxmox-host"
COMPONENT = "cnpg-existing-target-fault-client"
APP = "rehearsal_bootstrap"
DOMAIN = CLUSTER + "-rw." + NS + ".svc.cluster.local"
NATIVE = """SELECT jsonb_build_object('database',current_database(),'server',current_setting('server_version_num')::int,
 'cluster',current_setting('cluster_name'),'recovery',pg_is_in_recovery(),
 'synchronous_commit',current_setting('synchronous_commit'),'fsync',current_setting('fsync'),
 'full_page_writes',current_setting('full_page_writes'),'standby_names',current_setting('synchronous_standby_names'),
 'system_identifier',(pg_control_system()).system_identifier::text,'timeline',(pg_control_checkpoint()).timeline_id,
 'standbys',(SELECT coalesce(jsonb_agg(jsonb_build_object('name',application_name,'state',state,'sync_state',sync_state,
                 'flush_lsn',flush_lsn::text) ORDER BY application_name),'[]'::jsonb) FROM pg_stat_replication));"""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def exact_uid(value):
    require(isinstance(value, str) and str(UUID(value)) == value, "missing exact UID")
    return value


def ready(obj):
    return any(
        c.get("type") == "Ready" and c.get("status") == "True" for c in obj.get("status", {}).get("conditions", [])
    )


def contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(contains(a, b) for a, b in zip(actual, expected, strict=True))
        )
    return actual == expected


def selects(selector, labels):
    require(set(selector) <= {"matchLabels", "matchExpressions"}, "unknown network selector")
    if any(labels.get(k) != v for k, v in selector.get("matchLabels", {}).items()):
        return False
    for expr in selector.get("matchExpressions", []):
        key, op, values = expr["key"], expr["operator"], expr.get("values", [])
        require(op in ("In", "NotIn", "Exists", "DoesNotExist"), "unknown selector operation")
        if (
            (op == "In" and labels.get(key) not in values)
            or (op == "NotIn" and labels.get(key) in values)
            or (op == "Exists" and key not in labels)
            or (op == "DoesNotExist" and key in labels)
        ):
            return False
    return True


def bind(snapshot, *, degraded=False):
    cluster = snapshot["cluster"]
    require(
        (cluster["metadata"]["namespace"], cluster["metadata"]["name"], cluster["metadata"]["uid"])
        == (NS, CLUSTER, UID),
        "wrong existing Cluster identity",
    )
    spec = cluster["spec"]
    require(spec["imageName"] == IMAGE and spec["instances"] == 3, "operand/instance drift")
    require(
        spec["postgresql"]["synchronous"]
        == {"method": "any", "number": 1, "dataDurability": "required", "failoverQuorum": True},
        "native synchronous contract drift",
    )
    require(
        all(
            spec[key].get("size") == size and spec[key].get("storageClass") == "longhorn-v1-rwo"
            for key, size in (("storage", "30Gi"), ("walStorage", "10Gi"))
        ),
        "storage contract drift",
    )
    primary = cluster["status"]["currentPrimary"]
    pods = snapshot["pods"]["items"]
    nodes = {n["metadata"]["name"]: n for n in snapshot["nodes"]["items"]}
    records, claims = {}, set()
    for pod in pods:
        m, s = pod["metadata"], pod["spec"]
        require(m["namespace"] == NS and m["labels"]["cnpg.io/cluster"] == CLUSTER, "wrong scoped Pod")
        require(
            any(o.get("uid") == UID and o.get("kind") == "Cluster" for o in m.get("ownerReferences", [])),
            "Pod not owned by original Cluster",
        )
        require(re.fullmatch(re.escape(CLUSTER) + r"-[1-9]\d*", m["name"]), "unexpected Pod name")
        require(any(c["name"] == "postgres" and c["image"] == IMAGE for c in s["containers"]), "Pod operand drift")
        if not degraded:
            require(ready(pod) and not m.get("deletionTimestamp"), "baseline database Pod not stable Ready")
        node = nodes[s["nodeName"]]
        exact_uid(node["metadata"]["uid"])
        records[m["name"]] = {
            "uid": exact_uid(m["uid"]),
            "node": s["nodeName"],
            "node_uid": node["metadata"]["uid"],
            "domain": node["metadata"]["labels"][HOST],
            "pod_ip": pod["status"].get("podIP"),
            "ready": ready(pod),
            "deleting": bool(m.get("deletionTimestamp")),
        }
        claims |= {v["persistentVolumeClaim"]["claimName"] for v in s["volumes"] if "persistentVolumeClaim" in v}
    if not degraded:
        require(
            len(records) == 3 and len({r["domain"] for r in records.values()}) == 3 and primary in records,
            "baseline requires three distinct physical hosts and bound primary",
        )
    pvcs = snapshot["pvcs"]["items"]
    pvs = {v["metadata"]["name"]: v for v in snapshot["pvs"]["items"]}
    storage = {}
    for claim in pvcs:
        cm, cs = claim["metadata"], claim["spec"]
        require(cm["namespace"] == NS and cm["labels"]["cnpg.io/cluster"] == CLUSTER, "unscoped PVC")
        require(
            any(o.get("uid") == UID and o.get("kind") == "Cluster" for o in cm.get("ownerReferences", [])),
            "PVC not owned by original Cluster",
        )
        require(
            claim["status"]["phase"] == "Bound" and cs["storageClassName"] == "longhorn-v1-rwo",
            "PVC not bound/qualified",
        )
        volume = pvs[cs["volumeName"]]
        ref = volume["spec"]["claimRef"]
        require((ref["namespace"], ref["name"], ref["uid"]) == (NS, cm["name"], cm["uid"]), "PV/claim UID mismatch")
        require(volume["spec"]["csi"]["driver"] == "driver.longhorn.io", "wrong PV driver")
        storage[cm["name"]] = {
            "uid": exact_uid(cm["uid"]),
            "pv": cs["volumeName"],
            "pv_uid": exact_uid(volume["metadata"]["uid"]),
            "volume_handle": volume["spec"]["csi"]["volumeHandle"],
        }
    require(
        len(storage) == 6 and len(pvs) == 6 and (degraded or claims == set(storage)),
        "missing source data/WAL storage custody",
    )
    service = snapshot["service"]
    selector = service["spec"]["selector"]
    roles = [selector[k] for k in ("role", "cnpg.io/instanceRole") if k in selector]
    require(
        service["metadata"]["namespace"] == NS
        and service["metadata"]["name"] == CLUSTER + "-rw"
        and selector.get("cnpg.io/cluster") == CLUSTER
        and roles
        and all(r == "primary" for r in roles)
        and service["spec"].get("type", "ClusterIP") == "ClusterIP"
        and any(p.get("port") == p.get("targetPort") == 5432 for p in service["spec"]["ports"])
        and any(
            o.get("uid") == UID and o.get("kind") == "Cluster" for o in service["metadata"].get("ownerReferences", [])
        ),
        "wrong RW Service selector/owner/port",
    )
    exact_uid(service["metadata"]["uid"])
    return {
        "cluster_uid": UID,
        "primary": primary,
        "pods": records,
        "storage": storage,
        "service_uid": service["metadata"]["uid"],
    }


def quorum(binding, facts):
    require(set(facts) == set(binding["pods"]), "missing per-Pod native facts")
    writers = []
    systems = set()
    for name, fact in facts.items():
        require(
            fact["server"] == 160013 and fact["cluster"] == CLUSTER and fact["database"] == "postgres",
            "wrong native server/session",
        )
        require(
            all(fact[key] == "on" for key in ("synchronous_commit", "fsync", "full_page_writes")),
            "native durability disabled",
        )
        require(type(fact["recovery"]) is bool, "missing native recovery state")
        if not fact["recovery"]:
            writers.append(name)
        systems.add(fact["system_identifier"])
    require(
        writers == [binding["primary"]] and len(systems) == 1,
        "native requires one writable primary/two replicas/system identity",
    )
    standbys = facts[binding["primary"]]["standbys"]
    names = facts[binding["primary"]]["standby_names"]
    match = re.fullmatch(r"ANY\s+1\s*\(([^)]+)\)", names, re.IGNORECASE)
    require(
        match is not None and {s.strip().strip('"') for s in match[1].split(",")} == set(binding["pods"]),
        "native synchronous names differ",
    )
    require(
        {s["name"] for s in standbys} == set(binding["pods"]) - {binding["primary"]}
        and all(s["state"] == "streaming" and s["sync_state"] in ("sync", "quorum") for s in standbys),
        "missing actual two streaming synchronous/quorum standbys",
    )
    return {"system_identifier": systems.pop(), "timeline": facts[binding["primary"]]["timeline"]}


def render(snapshot, run_id):
    require(re.fullmatch(r"[a-z0-9]{8,32}", run_id), "invalid exclusive qualification ID")
    b = bind(snapshot)
    peer = next(
        r
        for name, r in sorted(b["pods"].items())
        if name != b["primary"] and r["domain"] != b["pods"][b["primary"]]["domain"]
    )
    labels = {"app.kubernetes.io/component": COMPONENT, "verdify.ai/qualification-run": run_id}
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "cnpg-existing-fault-" + run_id, "namespace": NS, "labels": labels},
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "nodeSelector": {"kubernetes.io/hostname": peer["node"], HOST: peer["domain"]},
            "imagePullSecrets": [{"name": "zot-origin-cluster-pull"}],
            "securityContext": {"runAsNonRoot": True, "runAsUser": 26, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [
                {
                    "name": "client",
                    "image": IMAGE,
                    "command": ["/bin/sh", "-c", "sleep 1800"],
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "resources": {"requests": {"cpu": "10m", "memory": "32Mi"}, "limits": {"memory": "128Mi"}},
                    "env": [
                        {"name": key, "value": value}
                        for key, value in {
                            "PGHOST": DOMAIN,
                            "PGDATABASE": APP,
                            "PGUSER": APP,
                            "PGCONNECT_TIMEOUT": "3",
                            "PGSSLMODE": "require",
                        }.items()
                    ]
                    + [
                        {
                            "name": "PGPASSWORD",
                            "valueFrom": {"secretKeyRef": {"name": CLUSTER + "-app", "key": "password"}},
                        },
                        {
                            "name": "VERDIFY_RECOVERY_CLIENT_POD_UID",
                            "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}},
                        },
                    ],
                }
            ],
        },
    }
    policy = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "cnpg-existing-fault-" + run_id, "namespace": NS},
        "spec": {
            "podSelector": {"matchLabels": labels},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": [
                {
                    "to": [{"podSelector": {"matchLabels": {"cnpg.io/cluster": CLUSTER}}}],
                    "ports": [{"protocol": "TCP", "port": 5432}],
                },
                {
                    "to": [
                        {
                            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                        }
                    ],
                    "ports": [{"protocol": proto, "port": 53} for proto in ("TCP", "UDP")],
                },
            ],
        },
    }
    ingress = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "cnpg-existing-fault-ingress-" + run_id, "namespace": NS},
        "spec": {
            "podSelector": {"matchLabels": {"cnpg.io/cluster": CLUSTER}},
            "policyTypes": ["Ingress"],
            "ingress": [
                {"from": [{"podSelector": {"matchLabels": labels}}], "ports": [{"protocol": "TCP", "port": 5432}]}
            ],
        },
    }
    ticket = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": b["pods"][b["primary"]]["uid"]}}
    return [policy, ingress, pod], b, ticket


def table(run_id):
    require(re.fullmatch(r"[a-z0-9]{8,32}", run_id), "invalid sentinel campaign")
    return "public.cnpg_recovery_" + run_id


def client_guard():
    return f"""DO $client_guard$ BEGIN
 IF current_database()<>'{APP}' OR session_user<>'{APP}' OR current_user<>session_user
    OR current_setting('cluster_name')<>'{CLUSTER}' OR current_setting('server_version_num')::int<>160013
    OR pg_is_in_recovery() OR inet_client_addr() IS NULL
    OR NOT coalesce((SELECT NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolbypassrls
                     FROM pg_roles WHERE rolname=session_user),false)
    OR current_setting('synchronous_commit')<>'on' OR current_setting('fsync')<>'on'
    OR current_setting('full_page_writes')<>'on'
    OR NOT coalesce((SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),false) THEN
   RAISE EXCEPTION 'existing target client refuses role/database/server/durability/TLS';
 END IF;
END $client_guard$;"""


def init_sql(run_id):
    relation = table(run_id)
    return f"""\\set ON_ERROR_STOP on
BEGIN;
{client_guard()}
DO $exclusive$ BEGIN IF to_regclass('{relation}') IS NOT NULL THEN
 RAISE EXCEPTION 'retain existing sentinel campaign; do not retry/reset'; END IF; END $exclusive$;
CREATE TABLE {relation}(seq bigint PRIMARY KEY CHECK(seq>=0),marker text NOT NULL);
REVOKE ALL ON TABLE {relation} FROM PUBLIC;
COMMIT;
"""


def check_table(run_id):
    relation = table(run_id)
    return f"""DO $sentinel_shape$ BEGIN
 IF NOT coalesce((SELECT relkind='r' AND relpersistence='p' AND NOT relrowsecurity
       AND NOT relforcerowsecurity AND NOT relispartition AND relowner='{APP}'::regrole
       FROM pg_class WHERE oid=to_regclass('{relation}')),false)
    OR EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('{relation}'))
    OR EXISTS(SELECT 1 FROM pg_rewrite WHERE ev_class=to_regclass('{relation}'))
    OR EXISTS(SELECT 1 FROM pg_inherits WHERE inhrelid=to_regclass('{relation}') OR inhparent=to_regclass('{relation}'))
    OR EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass('{relation}') AND attnum>0
              AND (attisdropped OR atthasdef OR attidentity<>'' OR attgenerated<>''))
    OR EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
              WHERE c.oid=to_regclass('{relation}') AND a.grantee<>'{APP}'::regrole)
    OR (SELECT array_agg(pg_get_constraintdef(oid) ORDER BY contype) FROM pg_constraint
        WHERE conrelid=to_regclass('{relation}')) IS DISTINCT FROM ARRAY['CHECK ((seq >= 0))','PRIMARY KEY (seq)']::text[]
    OR (SELECT array_agg(attname||':'||format_type(atttypid,atttypmod)||':'||attnotnull::text ORDER BY attnum)
       FROM pg_attribute WHERE attrelid=to_regclass('{relation}') AND attnum>0 AND NOT attisdropped)
         IS DISTINCT FROM ARRAY['seq:bigint:true','marker:text:true']::text[] THEN
   RAISE EXCEPTION 'sentinel shape drift';
 END IF;
END $sentinel_shape$;"""


def commit_sql(run_id, seq):
    require(type(seq) is int and seq >= 0, "invalid native sentinel sequence")
    relation, marker = table(run_id), run_id + "-" + str(seq)
    return f"""\\set ON_ERROR_STOP on
BEGIN;
{client_guard()}
{check_table(run_id)}
SET LOCAL statement_timeout='15s';
SET LOCAL lock_timeout='2s';
INSERT INTO {relation} VALUES({seq},'{marker}') ON CONFLICT(seq) DO NOTHING;
DO $idempotent$ BEGIN IF (SELECT marker FROM {relation} WHERE seq={seq}) IS DISTINCT FROM '{marker}' THEN
 RAISE EXCEPTION 'idempotent sentinel conflicts with retained row'; END IF; END $idempotent$;
COMMIT;
SELECT jsonb_build_object('kind','native-service-ack','seq',{seq},'marker','{marker}',
 'server_utc',clock_timestamp(),'flush_lsn',pg_current_wal_flush_lsn()::text,'server_addr',inet_server_addr()::text,
 'database',current_database(),'user',session_user,'cluster',current_setting('cluster_name'),
 'server',current_setting('server_version_num')::int,'recovery',pg_is_in_recovery());
"""


def probe_sql(run_id):
    return f"""\\set ON_ERROR_STOP on
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
{client_guard()}
{check_table(run_id)}
SELECT jsonb_build_object('kind','native-service-read','rows',(SELECT coalesce(jsonb_agg(jsonb_build_array(seq,marker)
 ORDER BY seq),'[]'::jsonb) FROM {table(run_id)}),'server_utc',clock_timestamp(),'server_addr',inet_server_addr()::text);
COMMIT;
"""


def json_line(output, kind=None):
    objects = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and (kind is None or value.get("kind") == kind):
            objects.append(value)
    require(len(objects) == 1, "missing unique native JSON result")
    return objects[0]


def ack(value, run_id, seq, binding):
    require(
        value.get("kind") == "native-service-ack"
        and value.get("seq") == seq
        and value.get("marker") == run_id + "-" + str(seq),
        "missing exact native ACK",
    )
    require(
        (value.get("database"), value.get("user"), value.get("cluster"), value.get("server"), value.get("recovery"))
        == (APP, APP, CLUSTER, 160013, False),
        "wrong ACK native identity",
    )
    require(value.get("server_addr") == binding["pods"][binding["primary"]]["pod_ip"], "ACK not current bound primary")
    require(re.fullmatch(r"[0-9A-F]+/[0-9A-F]+", value.get("flush_lsn", "")), "invalid native flush LSN")
    require(datetime.fromisoformat(value["server_utc"]).tzinfo is not None, "ACK lacks actual UTC")
    return value


def preserved(before, after):
    require(
        before["cluster_uid"] == after["cluster_uid"]
        and before["storage"] == after["storage"]
        and before["service_uid"] == after["service_uid"],
        "Cluster/storage/Service identity changed",
    )
    for name, pod in after["pods"].items():
        if name in before["pods"] and name != before["primary"]:
            require(pod["uid"] == before["pods"][name]["uid"], "surviving replica Pod replaced")
        require(
            any(
                pod["node"] == p["node"] and pod["node_uid"] == p["node_uid"] and pod["domain"] == p["domain"]
                for p in before["pods"].values()
            ),
            "node identity/domain drift",
        )


def promoted(before, after, facts):
    preserved(before, after)
    old = before["pods"][before["primary"]]
    require(all(p["uid"] != old["uid"] for p in after["pods"].values()), "old primary UID remains")
    require(
        len(after["pods"]) == 3
        and all(p["ready"] and not p["deleting"] for p in after["pods"].values())
        and len({p["domain"] for p in after["pods"].values()}) == 3,
        "membership not healthy three hosts",
    )
    require(after["pods"][after["primary"]]["domain"] != old["domain"], "primary not promoted on different host")
    return quorum(after, facts)


class Evidence:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        self.events = self.directory / "events.jsonl"
        self.number = 0

    def record(self, event):
        event = {"observer_utc": datetime.now(UTC).isoformat(), "observer_monotonic": time.monotonic(), **event}
        with self.events.open("a", encoding="utf-8") as stream:
            os.chmod(self.events, 0o600)
            stream.write(json.dumps(event, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return event

    def command(self, label, argv, stdin=None):
        self.number += 1
        start = self.record(
            {
                "event": "attempt",
                "number": self.number,
                "label": label,
                "argv": argv,
                "stdin_sha256": hashlib.sha256((stdin or "").encode()).hexdigest(),
            }
        )
        try:
            result = subprocess.run(argv, input=stdin, text=True, capture_output=True, timeout=25, check=False)
            code, stdout, stderr = result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired as exc:
            code = None
            stdout, stderr = exc.stdout or b"", exc.stderr or b""
            stdout = stdout.decode() if isinstance(stdout, bytes) else stdout
            stderr = stderr.decode() if isinstance(stderr, bytes) else stderr
        self.record(
            {
                "event": "result",
                "number": self.number,
                "label": label,
                "returncode": code,
                "stdout": stdout,
                "stderr": stderr,
                "unknown_commit_status": (label.startswith("commit") or label == "init-exclusive-sentinel")
                and code != 0,
            }
        )
        if code != 0:
            raise CommandError(label, code, stdout, stderr)
        return stdout, start


class CommandError(ValueError):
    def __init__(self, label, code, stdout, stderr):
        super().__init__(label + " failed; retained complete attempted evidence")
        self.code, self.stdout, self.stderr = code, stdout, stderr


def delete_unknown(error):
    """Only transport loss is ambiguous. Server rejection cannot qualify a fault."""
    if error.code is None:
        return True
    output = error.stdout + "\n" + error.stderr
    if re.search(r"Error from server|\"kind\"\s*:\s*\"Status\"", output, re.IGNORECASE):
        return False
    return bool(
        re.search(
            r"unexpected EOF|\bEOF\b|connection reset|connection closed|"
            r"i/o timeout|TLS handshake timeout|context deadline exceeded|"
            r"timed out|client\.timeout exceeded",
            output,
            re.IGNORECASE,
        )
    )


def validate_delete_response(output, name, uid):
    value = json.loads(output)
    if value.get("kind") == "Pod":
        m = value.get("metadata", {})
        require((m.get("namespace"), m.get("name"), m.get("uid")) == (NS, name, uid), "wrong DELETE response Pod")
        require(bool(m.get("deletionTimestamp")), "DELETE response lacks native termination state")
    elif value.get("kind") == "Status":
        details = value.get("details", {})
        require(
            value.get("status") == "Success"
            and details.get("name") == name
            and details.get("uid") == uid
            and details.get("kind") in ("pods", "Pod")
            and details.get("group", "") == ""
            and details.get("namespace", NS) == NS,
            "DELETE Status lacks exact successful UID/scope",
        )
    else:
        raise ValueError("DELETE response lacks native Pod/Status identity")


class ExistingTarget:
    def __init__(self, evidence):
        self.evidence = evidence
        self.base = ["kubectl", "--context", "vallery", "--request-timeout=15s", "-n", NS]

    def get(self, resource, name=None, selector=None):
        argv = self.base + ["get", resource]
        if name:
            argv.append(name)
        if selector:
            argv += ["-l", selector]
        return json.loads(self.evidence.command("read-" + resource, argv + ["-o", "json"])[0])

    def snapshot(self):
        values = {
            "cluster": self.get("clusters.postgresql.cnpg.io", CLUSTER),
            "pods": self.get("pods", selector="cnpg.io/cluster=" + CLUSTER),
            "nodes": self.get("nodes"),
            "pvcs": self.get("pvcs", selector="cnpg.io/cluster=" + CLUSTER),
            "service": self.get("service", CLUSTER + "-rw"),
        }
        values["pvs"] = {"items": [self.get("pv", c["spec"]["volumeName"]) for c in values["pvcs"]["items"]]}
        return values

    def sql(self, name, uid, query, *, client=False, label="native-read"):
        env = "VERDIFY_RECOVERY_CLIENT_POD_UID" if client else "VERDIFY_REHEARSAL_POD_UID"
        script = 'test "$' + env + '" = "$1" || exit 42; shift; exec "$@"'
        argv = self.base + [
            "exec",
            "-i",
            name,
            "-c",
            "client" if client else "postgres",
            "--",
            "sh",
            "-c",
            script,
            "uid-guard",
            uid,
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
        ]
        if not client:
            argv += ["-h", "/controller/run", "-U", "postgres", "-d", "postgres"]
        return self.evidence.command(label, argv, query)[0]

    def facts(self, binding):
        return {
            name: json_line(self.sql(name, p["uid"], "BEGIN READ ONLY;\n" + NATIVE + "\nCOMMIT;"))
            for name, p in binding["pods"].items()
        }

    def client(self, expected, expected_uid, baseline):
        live = self.get("pod", expected["metadata"]["name"])
        require(
            live["metadata"]["uid"] == expected_uid and ready(live) and not live["metadata"].get("deletionTimestamp"),
            "client UID/Ready drift",
        )
        for key in (
            "containers",
            "nodeSelector",
            "automountServiceAccountToken",
            "securityContext",
            "imagePullSecrets",
        ):
            require(contains(live["spec"][key], expected["spec"][key]), "client source contract drift: " + key)
        require(
            live["spec"]["containers"][0].get("securityContext")
            == expected["spec"]["containers"][0]["securityContext"],
            "client extra container privilege keys/capabilities",
        )
        require(
            not live["spec"].get("initContainers")
            and not live["spec"].get("ephemeralContainers")
            and not live["spec"].get("volumes")
            and not live["spec"].get("hostNetwork")
            and not live["spec"].get("hostPID")
            and not live["spec"].get("hostIPC")
            and not live["spec"]["containers"][0].get("envFrom")
            and not live["spec"]["containers"][0].get("args")
            and not live["spec"]["containers"][0].get("lifecycle")
            and not live["spec"]["containers"][0].get("livenessProbe")
            and not live["spec"]["containers"][0].get("readinessProbe")
            and not live["spec"]["containers"][0].get("startupProbe")
            and not live["spec"]["containers"][0].get("ports")
            and not live["spec"]["containers"][0].get("volumeMounts")
            and not live["spec"]["containers"][0].get("securityContext", {}).get("privileged", False),
            "client unexpected credential/host surface",
        )
        run_id = expected["metadata"]["labels"]["verdify.ai/qualification-run"]
        required_policy = "cnpg-existing-fault-" + run_id
        policies = self.get("networkpolicies")["items"]
        expected_policy = next(p for p in policies if p["metadata"]["name"] == required_policy)
        # Compare the source-owned policy; NetworkPolicies are additive, so an unrelated
        # policy selecting this client must not introduce further allowed traffic.
        require(
            expected_policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
            and expected_policy["spec"].get("ingress", []) == [],
            "client policy ingress drift",
        )
        source_egress = [
            {
                "to": [{"podSelector": {"matchLabels": {"cnpg.io/cluster": CLUSTER}}}],
                "ports": [{"protocol": "TCP", "port": 5432}],
            },
            {
                "to": [
                    {
                        "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                        "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                    }
                ],
                "ports": [{"protocol": proto, "port": 53} for proto in ("TCP", "UDP")],
            },
        ]
        require(
            expected_policy["spec"].get("egress") == source_egress
            and expected_policy["spec"]["podSelector"] == {"matchLabels": expected["metadata"]["labels"]},
            "client policy source drift",
        )
        for policy in policies:
            if selects(policy["spec"]["podSelector"], live["metadata"]["labels"]):
                require(
                    not policy["spec"].get("ingress")
                    and all(rule in source_egress for rule in policy["spec"].get("egress", [])),
                    "additive NetworkPolicy broadens client traffic",
                )
        node = self.get("node", live["spec"]["nodeName"])
        peer = next(p for p in baseline["pods"].values() if p["node"] == live["spec"]["nodeName"])
        require(
            node["metadata"]["uid"] == peer["node_uid"]
            and node["metadata"]["labels"][HOST] == peer["domain"]
            and peer["domain"] != baseline["pods"][baseline["primary"]]["domain"],
            "client physical host changed",
        )


def run(snapshot, run_id, client_uid, directory):
    manifests, before, ticket = render(snapshot, run_id)
    exact_uid(client_uid)
    evidence = Evidence(directory)
    target = ExistingTarget(evidence)
    client = manifests[-1]
    cname = client["metadata"]["name"]
    evidence.record(
        {
            "event": "bound-source",
            "binding": before,
            "ticket": ticket,
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
    )
    baseline = bind(target.snapshot())
    require(baseline == before, "rendered baseline drift; re-render before campaign")
    target.client(client, client_uid, before)
    native = quorum(before, target.facts(before))
    target.sql(cname, client_uid, init_sql(run_id), client=True, label="init-exclusive-sentinel")
    acknowledged = [
        ack(
            json_line(
                target.sql(cname, client_uid, commit_sql(run_id, 0), client=True, label="commit-baseline"),
                "native-service-ack",
            ),
            run_id,
            0,
            before,
        )
    ]
    # The persisted attempt precedes the only DELETE RPC. Unknown outcome is retained, never retried.
    old = before["pods"][before["primary"]]
    require(bind(target.snapshot()) == before, "baseline changed before fault")
    require(
        quorum(before, target.facts(before))["system_identifier"] == native["system_identifier"],
        "native pre-fault quorum/system drift",
    )
    target.client(client, client_uid, before)
    fault = evidence.record({"event": "fault-submit-once", "primary_uid": old["uid"], "delete_options": ticket})
    uri = "/api/v1/namespaces/" + NS + "/pods/" + before["primary"]
    try:
        output, _ = evidence.command(
            "delete-primary-once", target.base + ["delete", "--raw", uri, "-f", "-"], json.dumps(ticket)
        )
        validate_delete_response(output, before["primary"], old["uid"])
    except CommandError as exc:
        if not delete_unknown(exc):
            evidence.record({"event": "delete-definitively-rejected", "error": str(exc), "retry": False})
            raise ValueError("ordinary DELETE rejected; no fault qualification permitted") from exc
        evidence.record({"event": "delete-result-unknown-or-failed", "error": str(exc), "retry": False})
    deadline = fault["observer_monotonic"] + 600
    first = None
    consecutive = 0
    seq = 1
    while time.monotonic() < deadline:
        try:
            current = bind(target.snapshot(), degraded=True)
            preserved(before, current)
            target.client(client, client_uid, before)
            require(
                current["primary"] in current["pods"]
                and current["pods"][current["primary"]]["domain"] != old["domain"],
                "await different-host primary",
            )
            read = json_line(target.sql(cname, client_uid, probe_sql(run_id), client=True), "native-service-read")
            require(read["server_addr"] == current["pods"][current["primary"]]["pod_ip"], "read not current primary")
            rows = dict(read["rows"])
            require(all(rows.get(a["seq"]) == a["marker"] for a in acknowledged), "acknowledged sentinel absent")
            value = ack(
                json_line(
                    target.sql(
                        cname, client_uid, commit_sql(run_id, seq), client=True, label="commit-service-" + str(seq)
                    ),
                    "native-service-ack",
                ),
                run_id,
                seq,
                current,
            )
            acknowledged.append(value)
            success = evidence.record({"event": "acknowledged", "ack": value})
            if first is None:
                first = success
            seq += 1
            health = promoted(before, current, target.facts(current))
            require(health["system_identifier"] == native["system_identifier"], "native system identity changed")
            consecutive += 1
            if consecutive == 3:
                final = bind(target.snapshot())
                promoted(before, final, target.facts(final))
                finalread = json_line(
                    target.sql(cname, client_uid, probe_sql(run_id), client=True), "native-service-read"
                )
                require(
                    finalread["server_addr"] == final["pods"][final["primary"]]["pod_ip"], "final read primary changed"
                )
                retained = dict(finalread["rows"])
                require(all(retained.get(a["seq"]) == a["marker"] for a in acknowledged), "final ACK loss")
                result = {
                    "sentinel_fault": "qualified",
                    "scope": "one ordinary primary Pod loss; not host loss",
                    "rpo_acknowledged_rows_lost": 0,
                    "rto_observer_upper_bound_seconds": first["observer_monotonic"] - fault["observer_monotonic"],
                    "acknowledged": acknowledged,
                    "postpromotion_ordinary_authenticated_hot_sql": "pending",
                    "c5_complete": False,
                }
                evidence.record({"event": "result", **result})
                return result
        except (ValueError, KeyError, StopIteration) as exc:
            consecutive = 0
            evidence.record({"event": "failed-intermediate-round", "error": str(exc), "next_idempotent_sequence": seq})
        time.sleep(2)
    raise ValueError("finite fault observation expired; retain campaign and all unknown attempts")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("render", "run"))
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--client-uid")
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text())
    if args.action == "render":
        manifests, binding, ticket = render(snapshot, args.run_id)
        args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
        for name, value in (
            ("manifests.json", {"apiVersion": "v1", "kind": "List", "items": manifests}),
            ("binding.json", binding),
            ("primary-delete-options.json", ticket),
        ):
            (args.output / name).write_text(json.dumps(value, indent=2) + "\n")
    else:
        require(args.client_uid is not None, "ROOT run needs actual rendered client UID")
        print(json.dumps(run(snapshot, args.run_id, args.client_uid, args.output), indent=2))


if __name__ == "__main__":
    main()
