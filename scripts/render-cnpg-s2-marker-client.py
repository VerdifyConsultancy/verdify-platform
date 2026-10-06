"""Render a bounded S2-only actual TCP marker client using existing SecretRefs.

Each stdout record is native server output. BEGIN/start/xmin/COMMIT/ack and
server-clock boundaries are distinct statements. Does not apply or qualify.
"""

import argparse
import importlib.util
import ipaddress
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("s2_marker_profile", ROOT / "scripts/render-cnpg-s2-pitr-pair.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
TABLE = "public.cnpg_recovery_s2current274_20261006"


def render(cluster, primary, backup, admission, run_id):
    p.pitr.require(re.fullmatch(r"s2-[a-z0-9-]{8,60}", run_id), "invalid unique marker run identity")
    cm, pm = cluster["metadata"], primary["metadata"]
    p.pitr.require(
        cm["name"] == p.SOURCE
        and cm["uid"] == p.SOURCE_UID
        and cm["namespace"] == p.pitr.NS
        and cluster["spec"]["imageName"] == p.pitr.IMAGE
        and cluster["status"]["currentPrimary"] == pm["name"]
        and cluster["status"].get("readyInstances") == 3
        and pm["namespace"] == p.pitr.NS
        and primary["status"]["phase"] == "Running"
        and primary["metadata"]["labels"].get("cnpg.io/cluster") == p.SOURCE
        and admission.get("full_data_internal_catalog_accounting_complete") is True
        and admission.get("binding", {}).get("cluster_uid") == p.SOURCE_UID
        and admission.get("binding", {}).get("pod_uid") == pm["uid"]
        and admission.get("installation_sha256") == p.INSTALL_SHA,
        "S2 marker refuses unsealed source/primary",
    )
    p.pitr.uid(pm["uid"])
    ip = str(ipaddress.IPv4Address(primary["status"]["podIP"]))
    bm, bs, status = backup["metadata"], backup["spec"], backup["status"]
    p.pitr.uid(bm["uid"])
    p.pitr.require(
        bm["namespace"] == p.pitr.NS
        and bs["cluster"]["name"] == p.SOURCE
        and status["phase"] == "completed"
        and bs["method"] == "plugin"
        and bs["pluginConfiguration"]["name"] == p.pitr.PLUGIN
        and any(
            x.get("kind") == "Cluster" and x.get("uid") == p.SOURCE_UID and x.get("name") == p.SOURCE
            for x in bm.get("ownerReferences", [])
        )
        and re.fullmatch(r"[0-9]{8}T[0-9]{6}", status["backupId"]),
        "S2 marker requires exact completed owned Backup",
    )
    p.pitr.utc(status["stoppedAt"])
    sql = """\\set ON_ERROR_STOP on
SET timezone='UTC';
SET search_path=pg_catalog,public,pg_temp;
SET statement_timeout='10s';
SET lock_timeout='2s';
"""
    sql += f"""DO $marker_guard$
BEGIN
 IF current_database()<>'rehearsal_bootstrap' OR current_user<>'rehearsal_bootstrap'
    OR current_user<>session_user OR current_setting('cluster_name')<>'{p.SOURCE}'
    OR current_setting('server_version_num')::int<>160013 OR pg_is_in_recovery()
    OR current_setting('synchronous_commit')<>'on'
    OR inet_client_addr() IS NULL OR inet_server_addr()<>'{ip}'::inet
    OR clock_timestamp()<='{status["stoppedAt"]}'::timestamptz
    OR (SELECT oid FROM pg_database WHERE datname=current_database())<>16385
    OR (SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='{TABLE}'::regclass)<>'rehearsal_bootstrap'
    OR pg_walfile_name(pg_current_wal_flush_lsn()) IS NULL
    OR EXISTS (SELECT 1 FROM {TABLE} WHERE marker_id IN ('{run_id}-a','{run_id}-b','{run_id}-c')) THEN
   RAISE EXCEPTION 'S2 marker refuses endpoint/session/lineage/reused attempt';
 END IF;
END $marker_guard$;
"""
    for name in ("A", "B", "C"):
        marker_id = run_id + "-" + name.lower()
        sql += f"""BEGIN;
SELECT jsonb_build_object('kind','start','name','{name}',
 'transaction_started_at',clock_timestamp(),'server_address',pg_catalog.host(inet_server_addr()),
 'server_port',inet_server_port(),'backend_pid',pg_backend_pid());
INSERT INTO {TABLE}(marker_id,marker_name,payload)
VALUES ('{marker_id}','{name}',jsonb_build_object('contract','s2-current274','run','{run_id}','name','{name}'))
RETURNING jsonb_build_object('kind','insert','name','{name}','marker_id',marker_id,
 'xid',xmin::text,'payload',payload,'created_at',created_at);
COMMIT;
SELECT jsonb_build_object('kind','ack','name','{name}','acknowledged_at',clock_timestamp(),
 'acknowledged_flush_lsn',pg_current_wal_flush_lsn()::text,
 'timeline',('x'||left(pg_walfile_name(pg_current_wal_flush_lsn()),8))::bit(32)::bigint);
"""
        if name != "C":
            sql += f"SELECT jsonb_build_object('kind','boundary','name','{name}','target',clock_timestamp());\n"
    labels = {
        "app.kubernetes.io/part-of": "verdify",
        "app.kubernetes.io/component": "cnpg-s2-runtime-qualification",
        "verdify.ai/qualification-target": p.SOURCE,
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": run_id + "-markers", "namespace": p.pitr.NS, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 90,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "imagePullSecrets": [{"name": "zot-origin-cluster-pull"}],
                    "containers": [
                        {
                            "name": "marker-client",
                            "image": p.pitr.IMAGE,
                            "command": [
                                "sh",
                                "-c",
                                'printf "%s" "$1" | psql -X -qAt -v ON_ERROR_STOP=1',
                                "marker-sql",
                                sql,
                            ],
                            "env": [
                                {"name": "PGHOST", "value": p.SOURCE + "-rw." + p.pitr.NS + ".svc.cluster.local"},
                                {"name": "PGPORT", "value": "5432"},
                                {"name": "PGDATABASE", "value": "rehearsal_bootstrap"},
                                {"name": "PGUSER", "value": "rehearsal_bootstrap"},
                                {"name": "PGCONNECT_TIMEOUT", "value": "5"},
                                {
                                    "name": "PGPASSWORD",
                                    "valueFrom": {"secretKeyRef": {"name": p.SOURCE + "-app", "key": "password"}},
                                },
                            ],
                            "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"memory": "256Mi"}},
                            "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                        }
                    ],
                },
            },
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cluster", "primary", "backup", "admission", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    job = render(
        *(json.loads(getattr(args, n).read_text()) for n in ("cluster", "primary", "backup", "admission")), args.run_id
    )
    with args.output.open("x") as stream:
        yaml.safe_dump(job, stream, sort_keys=False)


if __name__ == "__main__":
    main()
