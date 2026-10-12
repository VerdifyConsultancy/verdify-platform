"""UID-bound primary-Pod loss on the existing isolated Verdify CNPG target.

Default emits a local plan only. --execute creates one restricted service client
and writes sentinels only in rehearsal_bootstrap. No Cluster, namespace, storage,
product database, Secret or device is created, changed or deleted. Unknown fault
submission stops without retry; all intermediate failures remain in custody.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NS = 'verdify-db-rehearsal'
CLUSTER = 'verdify-cnpg-rehearsal'
DATABASE = 'rehearsal_bootstrap'
DOMAIN = 'topology.vallery.net/proxmox-host'
DIGEST = 'sha256:8b461e37d18aa049704eb6a9cde2ba9af0f955f8d3725e921070d2450bb2f137'
IMAGE = 'registry.vallery.net/verdifyconsultancy/verdify-timescaledb-cnpg:16.13-ts2.25.2@' + DIGEST
LABELS = {'app.kubernetes.io/part-of': 'verdify', 'app.kubernetes.io/component': 'cnpg-rehearsal'}
K = ['kubectl', '--context', 'vallery', '--request-timeout=20s']


def require(ok, message):
    if not ok:
        raise ValueError(message)


def ready(obj):
    return any(x['type'] == 'Ready' and x['status'] == 'True' for x in obj.get('status', {}).get('conditions', []))


def run(argv, *, data=None, timeout=30):
    result = subprocess.run(argv, input=data, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, 'Native operation unavailable; raw diagnostics retained privately by caller')
    return result.stdout


def get(kind, name=None, namespace=NS):
    command = K + (['-n', namespace] if namespace else []) + ['get', kind]
    return json.loads(run(command + ([name] if name else []) + ['-o', 'json']))


def save(path, obj):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(obj, indent=2) + '\n')
    temp.replace(path)


def validate_binding(binding):
    require(set(binding) == {'cluster_uid', 'primary', 'primary_uid', 'operand_digest', 'nodes'}, 'Exact target binding required')
    for value in [binding['cluster_uid'], binding['primary_uid']]:
        require(isinstance(value, str) and bool(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value)), 'Invalid target UID')
    require(bool(re.fullmatch(r'verdify-cnpg-rehearsal-[1-9]\d*', binding['primary'])), 'Wrong primary name')
    require(binding['operand_digest'] == DIGEST, 'Unqualified operand')
    require(len(binding['nodes']) == 3 and len({x['domain'] for x in binding['nodes'].values()}) == 3, 'Three bound physical domains required')
    require(all(set(x) == {'uid', 'domain'} and x['uid'] and x['domain'] for x in binding['nodes'].values()), 'Exact node UID/domain required')


def topology(binding, *, initial=False):
    cluster = get('cluster', CLUSTER)
    require(cluster['metadata']['uid'] == binding['cluster_uid'] and cluster['metadata']['namespace'] == NS, 'Cluster identity changed')
    spec = cluster['spec']
    require(spec['instances'] == 3 and spec['imageName'] == IMAGE, 'Target spec changed')
    require(spec['postgresql']['synchronous'] == {'method': 'any', 'number': 1, 'dataDurability': 'required', 'failoverQuorum': True}, 'Synchronous durability changed')
    require(spec['postgresql']['parameters']['synchronous_commit'] == 'on', 'Synchronous commit disabled')
    require(spec['bootstrap']['initdb'] == {'database': DATABASE, 'owner': DATABASE}, 'Bootstrap database/identity changed')
    pods = [p for p in get('pods')['items'] if p['metadata'].get('labels', {}).get('cnpg.io/cluster') == CLUSTER and ready(p) and not p['metadata'].get('deletionTimestamp')]
    require(len(pods) == 3, 'Three ready operands required')
    nodes = {n['metadata']['name']: n for n in get('nodes', namespace=None)['items']}
    require({p['spec']['nodeName'] for p in pods} == set(binding['nodes']), 'Bound node placement changed')
    for p in pods:
        require(any(x.get('kind') == 'Cluster' and x.get('uid') == binding['cluster_uid'] and x.get('controller') is True for x in p['metadata'].get('ownerReferences', [])), 'Operand owner changed')
        st = [c for c in p['status']['containerStatuses'] if c['name'] == 'postgres']
        require(len(st) == 1 and st[0]['ready'] and st[0]['imageID'].endswith('@' + DIGEST), 'Operand digest/readiness changed')
        n = nodes[p['spec']['nodeName']]
        require(ready(n) and n['metadata']['uid'] == binding['nodes'][n['metadata']['name']]['uid'] and n['metadata']['labels'].get(DOMAIN) == binding['nodes'][n['metadata']['name']]['domain'], 'Physical node identity changed')
    primary = next(p for p in pods if p['metadata']['name'] == cluster['status']['currentPrimary'])
    if initial:
        require(primary['metadata']['name'] == binding['primary'] and primary['metadata']['uid'] == binding['primary_uid'], 'Stale primary authority')
    return cluster, pods, primary


def storage():
    claims = [c for c in get('pvc')['items'] if any(x.get('kind') == 'Cluster' and x.get('name') == CLUSTER for x in c['metadata'].get('ownerReferences', []))]
    require(len(claims) == 6 and all(c['status']['phase'] == 'Bound' for c in claims), 'Six bound data/WAL claims required')
    return {c['metadata']['name']: {'uid': c['metadata']['uid'], 'pv': c['spec']['volumeName'], 'pv_uid': get('pv', c['spec']['volumeName'], namespace=None)['metadata']['uid']} for c in claims}


def pod_sql(pod, sql, *, database=DATABASE, client=False):
    uid = pod['metadata']['uid']
    guard = 'test "$VERDIFY_REHEARSAL_POD_UID" = "$1" || exit 42; shift; exec "$@"'
    command = K + ['-n', NS, 'exec', '-i', pod['metadata']['name'], '-c', 'client' if client else 'postgres', '--', 'sh', '-c', guard, 'uid-guard', uid, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1']
    if not client:
        command += ['-h', '/controller/run', '-U', 'postgres', '-d', database]
    return run(command, data=sql, timeout=135).strip()


def client_manifest(binding, peer, run_id):
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'cnpg-recovery-' + run_id, 'namespace': NS, 'labels': LABELS}, 'spec': {
        'restartPolicy': 'Never', 'automountServiceAccountToken': False, 'activeDeadlineSeconds': 1200,
        'nodeSelector': {'kubernetes.io/hostname': peer['spec']['nodeName']},
        'imagePullSecrets': [{'name': 'zot-origin-cluster-pull'}],
        'securityContext': {'runAsNonRoot': True, 'runAsUser': 26, 'seccompProfile': {'type': 'RuntimeDefault'}},
        'containers': [{'name': 'client', 'image': IMAGE, 'command': ['sleep', '1200'],
            'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
            'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'memory': '128Mi'}},
            'env': [{'name': 'PGHOST', 'value': CLUSTER + '-rw'}, {'name': 'PGDATABASE', 'value': DATABASE}, {'name': 'PGUSER', 'value': DATABASE}, {'name': 'PGCONNECT_TIMEOUT', 'value': '3'},
                {'name': 'PGOPTIONS', 'value': '-c statement_timeout=10000 -c lock_timeout=2000'},
                {'name': 'PGPASSWORD', 'valueFrom': {'secretKeyRef': {'name': CLUSTER + '-app', 'key': 'password'}}},
                {'name': 'VERDIFY_REHEARSAL_POD_UID', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.uid'}}}]}]}}


def delete_exact(pod, path):
    options = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': pod['metadata']['uid']}}
    save(path, options)
    # Native kubectl raw DELETE sends this exact body through existing context transport.
    return run(K + ['delete', '--raw=/api/v1/namespaces/' + NS + '/pods/' + pod['metadata']['name'], '-f', str(path)])


def witness(pod):
    spec = importlib.util.spec_from_file_location('cnpg_c0', ROOT / 'scripts/cnpg-c0-restore-qualification.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text = pod_sql(pod, module.emit_sql(target=True), database='verdify_rehearsal')
    return next(json.loads(line) for line in text.splitlines() if line.startswith('{'))


def execute(binding, directory, *, clock=time.monotonic, sleep=time.sleep):
    os.umask(0o077)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    record = {'scope': 'Existing isolated cluster ordinary primary-Pod loss, not host loss or production cutover', 'binding': binding, 'status': 'preflight', 'observations': [], 'acknowledged': 0, 'fault_outcome': 'not-attempted', 'no_blind_retry': True}
    def persist():
        save(directory / 'receipt.json', record)
    persist()
    try:
        _, pods, primary = topology(binding, initial=True)
        claims = storage()
        before = witness(primary)
        save(directory / 'product-witness-before.json', before)
        require(pod_sql(primary, "SELECT current_setting('server_version_num')||'|'||current_setting('cluster_name')||'|'||pg_is_in_recovery()||'|'||current_database()||'|'||(inet_client_addr() IS NULL)") == '160013|' + CLUSTER + '|false|' + DATABASE + '|true', 'Wrong local primary/server/database')
        require(int(pod_sql(primary, "SELECT count(*) FROM pg_stat_replication WHERE state='streaming' AND sync_state IN ('sync','quorum')")) >= 1, 'No synchronous streaming standby')
        peer = next(p for p in pods if p['spec']['nodeName'] != primary['spec']['nodeName'])
        run_id = uuid.uuid4().hex[:12]
        manifest = client_manifest(binding, peer, run_id)
        save(directory / 'client-manifest.json', manifest)
        record['client_create_outcome'] = 'unknown'; persist()
        client = json.loads(run(K + ['create', '-f', '-'], data=json.dumps(manifest)))
        record['client_uid'] = client['metadata']['uid']; record['client_name'] = client['metadata']['name']; record['client_create_outcome'] = 'accepted'; persist()
        limit = clock() + 120
        while not ready(client):
            require(clock() < limit, 'Service client not ready; retain existing target')
            sleep(3)
            client = get('pod', record['client_name'])
            require(client['metadata']['uid'] == record['client_uid'], 'Service client replaced')
        require(pod_sql(client, "SELECT current_database()||'|'||current_user||'|'||session_user||'|'||current_setting('synchronous_commit')||'|'||current_setting('fsync')||'|'||current_setting('full_page_writes')", client=True) == DATABASE + '|' + DATABASE + '|' + DATABASE + '|on|on|on', 'Service bootstrap auth/durability refused')
        table = 'qualification_' + run_id
        record['sentinel_table'] = table
        def commit(serial):
            value = pod_sql(client, f"BEGIN; INSERT INTO {table} VALUES ({serial},'{run_id}') ON CONFLICT(id) DO UPDATE SET marker=excluded.marker; COMMIT; SELECT json_build_object('count',(SELECT count(*) FROM {table} WHERE marker='{run_id}'),'lsn',pg_current_wal_flush_lsn()::text,'at',clock_timestamp());", client=True)
            ack = json.loads(next(x for x in value.splitlines() if x.startswith('{')))
            require(ack['count'] == serial + 1, 'Acknowledged sentinel lineage mismatch')
            return ack
        pod_sql(client, f'CREATE TABLE {table}(id integer PRIMARY KEY,marker text NOT NULL);', client=True)
        ack = commit(0)
        record['acknowledged'] = 1; record['pre_fault_ack'] = ack; record['storage_before'] = claims; persist()
        # Rebind immediately before destructive effect; never delete an observed successor.
        _, _, actual_primary = topology(binding, initial=True)
        require(actual_primary['metadata']['uid'] == primary['metadata']['uid'] and storage() == claims, 'Target custody changed before fault')
        started = clock(); record['fault_started_monotonic'] = started; record['fault_outcome'] = 'unknown'; record['status'] = 'fault-attempted'; persist()
        delete_exact(primary, directory / 'primary-delete-options.json')
        record['fault_outcome'] = 'delete-response-received'; persist()
        stable = 0
        while clock() - started <= 600:
            observation = {'elapsed_s': clock() - started, 'passed': False}
            try:
                _, live, promoted = topology(binding)
                require(not any(p['metadata']['uid'] == binding['primary_uid'] for p in get('pods')['items']), 'Original primary UID still present')
                require(promoted['spec']['nodeName'] != primary['spec']['nodeName'], 'Primary not promoted to another bound host')
                roles = {p['metadata']['name']: pod_sql(p, 'SELECT pg_is_in_recovery()') for p in live}
                require(list(roles.values()).count('f') == 1 and list(roles.values()).count('t') == 2 and roles[promoted['metadata']['name']] == 'f', 'One writer/two replicas not restored')
                require(storage() == claims, 'PVC/PV custody changed')
                require(int(pod_sql(client, f"SELECT count(*) FROM {table} WHERE marker='{run_id}'", client=True)) == record['acknowledged'], 'Acknowledged marker loss')
                ack = commit(record['acknowledged'])
                record['acknowledged'] = ack['count']
                observation.update(passed=True, ack=ack, roles=roles, promoted_uid=promoted['metadata']['uid'])
                if 'first_service_recovery_s' not in record:
                    record['first_service_recovery_s'] = clock() - started
                stable += 1
            except (ValueError, subprocess.TimeoutExpired, StopIteration) as error:
                stable = 0; observation['error_class'] = type(error).__name__
            record['observations'].append(observation); persist()
            if stable >= 3:
                after = witness(promoted)
                save(directory / 'product-witness-after.json', after)
                require(before == after, 'Protected product witness changed')
                record.update(status='passed', rpo_acknowledged_rows_lost=0, qualified_recovery_s=clock() - started, product_witness_unchanged=True, storage_unchanged=True)
                persist()
                return
            sleep(10)
        raise ValueError('Recovery target missed; no second fault or target replacement')
    except Exception as error:
        record.update(status='failed-or-unknown', error_class=type(error).__name__, rollback_not_inferred=True)
        persist()
        raise ValueError('Recovery unavailable; inspect custody, do not blindly retry') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binding', type=Path, required=True)
    parser.add_argument('--binding-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--receipt-dir', type=Path)
    args = parser.parse_args()
    require(args.binding.is_file() and not args.binding.is_symlink(), 'Regular binding custody required')
    raw = args.binding.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == args.binding_sha256, 'Binding custody mismatch')
    binding = json.loads(raw); validate_binding(binding)
    with args.output.open('x') as stream:
        json.dump({'binding': binding, 'namespace': NS, 'cluster': CLUSTER, 'sentinel_database': DATABASE, 'fault': 'one UID-preconditioned ordinary primary Pod DELETE', 'credential': 'existing bootstrap app Secret reference only', 'execute': args.execute, 'no_product_device_or_storage_mutation': True}, stream, indent=2)
    if args.execute:
        require(args.receipt_dir is not None, 'Private custody directory required')
        execute(binding, args.receipt_dir)
    else:
        require(args.receipt_dir is None, 'No execution receipt in plan mode')


if __name__ == '__main__':
    main()
