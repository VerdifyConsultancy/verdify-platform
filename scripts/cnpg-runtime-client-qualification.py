"""Render target-only authenticated ordinary-client Jobs; never execute them.

ROOT must adopt target-only credentials and reviewed native admission first.
A ROOT-held before/after Cluster/Pod/Service binding complements each actual
client's database identity and backend address readback. No service/device loop
is started, and no production credential or endpoint is accepted.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NS = "verdify-db-rehearsal"
CLUSTER = "verdify-cnpg-rehearsal"
DATABASE = "verdify_rehearsal"
HOST = CLUSTER + "-rw." + NS + ".svc.cluster.local"
ROLES = {name: f"verdify_{name}_runtime_login" for name in ("api", "ingestor", "mcp")}

# Execute inside the actual reviewed consumer image, not a substitute psql image.
PROBE = r'''
import asyncio, importlib.util, json, logging, os, pathlib, sys, urllib.parse
logging.disable(logging.CRITICAL)
binding = json.loads(os.environ['QUALIFICATION_BINDING'])
role = binding['consumer']
login = binding['login']
password = os.environ['DB_PASSWORD']
assert password and os.environ['DB_USER'] == login
baked = os.environ.get('VERDIFY_GIT_SHA', '')
fallback_path = pathlib.Path('/etc/verdify/source-revision')
fallback = fallback_path.read_text().strip() if role == 'api' and fallback_path.is_file() else ''
valid_baked = len(baked) == 40 and all(c in '0123456789abcdef' for c in baked)
valid_fallback = len(fallback) == 40 and all(c in '0123456789abcdef' for c in fallback)
assert not (valid_baked and valid_fallback and baked != fallback)
assert (baked if valid_baked else fallback if valid_fallback else '') == binding['consumer_source']
assert os.environ['DB_HOST'] == 'verdify-cnpg-rehearsal-rw.verdify-db-rehearsal.svc.cluster.local'
assert os.environ['DB_NAME'] == 'verdify_rehearsal'
assert os.environ['VERDIFY_DEVICE_WRITE_ENABLED'] == '0'
assert not any(os.environ.get(k) for k in ('POSTGRES_PASSWORD','DB_PASS','ESP32_API_KEY','DATABASE_URL'))
dsn = 'postgresql://' + login + ':' + urllib.parse.quote(password, safe='') + '@' + os.environ['DB_HOST'] + ':5432/verdify_rehearsal?default_transaction_read_only=on&search_path=pg_catalog%2C%20public%2C%20pg_temp'
os.environ['DB_DSN'] = dsn
path = pathlib.Path(binding['image_module_path'])
assert hashlib_sha(path.read_bytes()) == binding['consumer_module_sha256']
sys.path.insert(0, str(path.parent))
spec = importlib.util.spec_from_file_location('qualified_consumer', path)
consumer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = consumer
spec.loader.exec_module(consumer)

async def run():
    import asyncpg
    pool = None
    try:
        if role == 'mcp':
            pool = await consumer._kpi_fanout_pool_get()
        else:
            options = {'init': consumer._init_db_connection, 'setup': consumer._setup_db_connection} if role == 'api' else {}
            pool = await asyncpg.create_pool(dsn, min_size=1 if role == 'api' else 2,
                max_size=3 if role == 'api' else 10, **options)
            if role == 'ingestor':
                await consumer.attest_ordinary_ingestor_runtime_role(pool)
        for checkout in range(2):
            async with pool.acquire() as conn:
                identity = dict(await conn.fetchrow("""SELECT current_user::text AS current_user,
                    session_user::text AS session_user, current_database() AS database,
                    current_setting('server_version_num') AS server_version,
                    current_setting('cluster_name') AS cluster_name,
                    current_setting('default_transaction_read_only') AS default_read_only,
                    current_setting('transaction_read_only') AS transaction_read_only,
                    inet_server_addr()::text AS backend_address_raw,
                    pg_catalog.host(inet_server_addr()) AS backend_address, pg_is_in_recovery() AS replica"""))
                assert identity['current_user'] == identity['session_user'] == login
                assert identity['database'] == 'verdify_rehearsal' and identity['server_version'] == '160013'
                assert identity['cluster_name'] == 'verdify-cnpg-rehearsal' and identity['replica'] is False
                assert identity['backend_address'] == binding['primary_address']
                assert identity['default_read_only'] == identity['transaction_read_only'] == 'on'
                async with conn.transaction(readonly=True):
                    rows = await conn.fetch(binding['hot_sql'])
        result = {'status':'passed', 'consumer':role, 'identity':identity,
            'hot_query_row_count':len(rows), 'target_binding_sha256':binding['target_binding_sha256'],
            'consumer_source':binding['consumer_source'], 'consumer_image':binding['consumer_image'],
            'profile_sha256':binding['profile_sha256'], 'password_authentication':'actual asyncpg TCP pool login',
            'pool_checkout_count':2, 'service_process_started':False}
    finally:
        if pool is not None:
            await pool.close()
    print(json.dumps(result))

asyncio.run(run())
'''
# Keep every exception private: DSNs/passwords can occur in driver/import errors.
WRAPPED_PROBE = """import hashlib, json, sys

def hashlib_sha(value):
    return hashlib.sha256(value).hexdigest()

try:
    exec(compile(PROBE_TEXT, '<verdify-cnpg-runtime-probe>', 'exec'))
except BaseException as exc:
    probe_line = None
    trace = exc.__traceback__
    while trace is not None:
        if trace.tb_frame.f_code.co_filename == '<verdify-cnpg-runtime-probe>':
            probe_line = trace.tb_lineno
        trace = trace.tb_next
    print(json.dumps({'status':'failed','error_category':type(exc).__name__, 'probe_line':probe_line}))
    sys.exit(1)
""".replace("PROBE_TEXT", repr(PROBE))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def validate_binding(binding):
    require(set(binding) == {"cluster", "pod", "service"}, "exact native Cluster/Pod/Service binding required")
    cluster, pod, service = (binding[k] for k in ("cluster", "pod", "service"))
    spec = importlib.util.spec_from_file_location("qualified_restore", ROOT / "scripts/cnpg-paired-restore.py")
    operator = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = operator
    spec.loader.exec_module(operator)
    operator.target_identity(cluster, pod, cluster_uid=cluster["metadata"]["uid"], pod_uid=pod["metadata"]["uid"])
    require(cluster["status"]["currentPrimary"] == pod["metadata"]["name"], "bound Pod must be current primary")
    require(not pod["metadata"].get("deletionTimestamp"), "terminating primary refused")
    require(
        service["metadata"]["namespace"] == NS and service["metadata"]["name"] == CLUSTER + "-rw",
        "target RW Service required",
    )
    require(service["spec"].get("type", "ClusterIP") == "ClusterIP", "external Service refused")
    require(service["spec"]["selector"].get("cnpg.io/cluster") == CLUSTER, "wrong Service cluster")
    require(
        any(
            owner.get("kind") == "Cluster" and owner.get("uid") == cluster["metadata"]["uid"]
            for owner in service["metadata"].get("ownerReferences", [])
        ),
        "wrong Service owner",
    )
    primary_selectors = [
        service["spec"]["selector"][key]
        for key in ("role", "cnpg.io/instanceRole")
        if key in service["spec"]["selector"]
    ]
    require(
        primary_selectors and all(value == "primary" for value in primary_selectors), "wrong Service primary selector"
    )
    require(
        any(p["port"] == 5432 and p.get("targetPort", 5432) == 5432 for p in service["spec"]["ports"]),
        "wrong Service port",
    )
    for obj in (cluster, pod, service):
        uuid.UUID(obj["metadata"]["uid"])
    require(re.fullmatch(r"10\.42\.\d{1,3}\.\d{1,3}", pod["status"]["podIP"]), "expected native Pod address")
    return hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def render(binding, consumer, image, source, module_sha, profile_sha, suffix):
    binding_sha = validate_binding(binding)
    require(consumer in ROLES, "ordinary consumer required")
    require(
        re.fullmatch(r"registry\.vallery\.net/verdifyconsultancy/verdify-" + consumer + r"@sha256:[0-9a-f]{64}", image),
        "origin consumer digest required",
    )
    require(re.fullmatch(r"[0-9a-f]{40}", source), "exact consumer source required")
    require(
        all(re.fullmatch(r"[0-9a-f]{64}", h) for h in (module_sha, profile_sha)),
        "reviewed source/profile hashes required",
    )
    require(re.fullmatch(r"[a-z0-9]{8,20}", suffix), "unique bounded Job suffix required")
    paths = {"api": "api/main.py", "ingestor": "ingestor/ingestor.py", "mcp": "mcp/server.py"}
    require(
        hashlib.sha256((ROOT / paths[consumer]).read_bytes()).hexdigest() == module_sha,
        "local consumer source custody mismatch",
    )
    facts = {
        "consumer": consumer,
        "login": ROLES[consumer],
        "consumer_source": source,
        "consumer_image": image,
        "consumer_module_sha256": module_sha,
        "source_module_path": paths[consumer],
        "image_module_path": {
            "api": "/app/main.py",
            "ingestor": "/app/ingestor/ingestor.py",
            "mcp": "/app/mcp/server.py",
        }[consumer],
        "profile_sha256": profile_sha,
        "target_binding_sha256": binding_sha,
        "primary_address": binding["pod"]["status"]["podIP"],
        "hot_sql": hot_query(consumer),
    }
    labels = {"app.kubernetes.io/part-of": "verdify", "app.kubernetes.io/component": "cnpg-runtime-qualification"}
    env = [
        {"name": k, "value": v}
        for k, v in {
            "QUALIFICATION_BINDING": json.dumps(facts, sort_keys=True),
            "DB_HOST": HOST,
            "DB_PORT": "5432",
            "DB_NAME": DATABASE,
            "DB_USER": ROLES[consumer],
            "VERDIFY_DEVICE_WRITE_ENABLED": "0",
            "VERDIFY_" + consumer.upper() + "_RUNTIME_DB_ROLE_REQUIRED": "1",
        }.items()
    ]
    env.append(
        {
            "name": "DB_PASSWORD",
            "valueFrom": {
                "secretKeyRef": {"name": "verdify-cnpg-rehearsal-" + consumer + "-client-auth", "key": "password"}
            },
        }
    )
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "cnpg-runtime-" + consumer + "-" + suffix, "namespace": NS, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 180,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        # Match the shipped appuser and its private HOME; asyncpg
                        # resolves ~/.postgresql paths before opening its pool.
                        "runAsUser": 1000,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "imagePullSecrets": [{"name": "zot-origin-cluster-pull"}],
                    "containers": [
                        {
                            "name": "client",
                            "image": image,
                            "command": ["python", "-I", "-c", WRAPPED_PROBE],
                            "env": env,
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"memory": "1Gi"}},
                        }
                    ],
                },
            },
        },
    }


def hot_query(consumer):
    path = {"api": "api/main.py", "ingestor": "ingestor/ingestor.py", "mcp": "mcp/server.py"}[consumer]
    tree = ast.parse((ROOT / path).read_text())
    if consumer == "mcp":
        tree = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "climate")
    values = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    if consumer == "api":
        exact = "SELECT temp_low, temp_high, vpd_low, vpd_high, temp_target, vpd_target FROM fn_band_setpoints(now())"
        matches = [value for value in values if value == exact]
    elif consumer == "ingestor":
        matches = [value for value in values if value == "SELECT max(ts) FROM climate"]
    else:
        matches = [
            value
            for value in values
            if "SELECT round(temp_avg::numeric,1)" in value and "FROM climate ORDER BY ts DESC LIMIT 1" in value
        ]
    require(len(set(matches)) == 1, "exact consumer hot query missing or ambiguous")
    return matches[0]


def policies():
    client = {"app.kubernetes.io/part-of": "verdify", "app.kubernetes.io/component": "cnpg-runtime-qualification"}
    database = {"cnpg.io/cluster": CLUSTER}
    return [
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "cnpg-runtime-client-only", "namespace": NS},
            "spec": {
                "podSelector": {"matchLabels": client},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": [
                    {"to": [{"podSelector": {"matchLabels": database}}], "ports": [{"protocol": "TCP", "port": 5432}]},
                    {
                        "to": [
                            {
                                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                            }
                        ],
                        "ports": [{"protocol": protocol, "port": 53} for protocol in ("TCP", "UDP")],
                    },
                ],
            },
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "cnpg-runtime-client-ingress", "namespace": NS},
            "spec": {
                "podSelector": {"matchLabels": database},
                "policyTypes": ["Ingress"],
                "ingress": [
                    {"from": [{"podSelector": {"matchLabels": client}}], "ports": [{"protocol": "TCP", "port": 5432}]}
                ],
            },
        },
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--binding-sha256", required=True)
    parser.add_argument("--consumer", choices=ROLES, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--module-sha256", required=True)
    parser.add_argument("--profile-sha256", required=True)
    parser.add_argument("--suffix", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.binding.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == args.binding_sha256, "raw binding custody mismatch")
    job = render(
        json.loads(raw), args.consumer, args.image, args.source, args.module_sha256, args.profile_sha256, args.suffix
    )
    with args.output.open("x") as out:
        json.dump({"apiVersion": "v1", "kind": "List", "items": [*policies(), job]}, out, indent=2)
        out.write("\n")


if __name__ == "__main__":
    main()
