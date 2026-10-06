"""Prepare bounded independent reader retrieval of the S2 backup and marker WAL."""

import argparse
import importlib.util
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("s2_archive_profile", ROOT / "scripts/render-cnpg-s2-pitr-pair.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
IMAGE = "ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar:v0.14.0@sha256:9880817c285c7afa4d195da2145064d21907405489ed6ec39abe59b1feb558a4"
READER = r"""
import boto3,os,json,hashlib,logging,sys
logging.disable(logging.CRITICAL)
try:
 expected=json.loads(os.environ['EXPECTED_ARCHIVE'])
 s=boto3.client('s3',endpoint_url='https://s3-hdd.vallery.net')
 bucket='verdify-cnpg-rehearsal';prefix='postgresql/verdify-cnpg-s2/'
 objects=[]
 for page in s.get_paginator('list_objects_v2').paginate(Bucket=bucket,Prefix=prefix):
  objects.extend(page.get('Contents',[]))
 base=[x for x in objects if x['Key'].startswith(prefix+'base/'+expected['backup_id']+'/')]
 assert base and any(x['Key'].endswith('/backup.info') for x in base)
 assert any('.tar' in x['Key'] for x in base)
 heads=[]
 for x in base:
  h=s.head_object(Bucket=bucket,Key=x['Key']);assert h['ContentLength']==x['Size'] and x['Size']>0
  heads.append({'key':x['Key'],'bytes':h['ContentLength']})
 required=[x for x in base if x['Key'].endswith('/backup.info')]
 for wal in expected['wal_names']:
  matches=[x for x in objects if x['Key'].startswith(prefix+'wals/') and x['Key'].rsplit('/',1)[-1] in (wal,wal+'.gz')]
  assert len(matches)==1
  required.extend(matches)
 receipts=[]
 for x in required:
  r=s.get_object(Bucket=bucket,Key=x['Key']);h=hashlib.sha256();n=0
  for block in r['Body'].iter_chunks(2*1024*1024):h.update(block);n+=len(block)
  assert n==x['Size'] and n>0
  receipts.append({'key':x['Key'],'bytes':n,'sha256':h.hexdigest()})
 print(json.dumps({'schema':'cnpg-s2-independent-archive-reader-v1','server_name':'verdify-cnpg-s2',
  'backup_uid':expected['backup_uid'],'backup_id':expected['backup_id'],'required_wals':expected['wal_names'],
  'base_object_heads':heads,'retrieved':receipts,'reader_only_secret_ref':True,'complete':True}),flush=True)
except Exception as e:
 print(json.dumps({'schema':'cnpg-s2-independent-archive-reader-v1','complete':False,'error_type':type(e).__name__}),flush=True)
 sys.exit(1)
"""


def render(cluster, backup, custody, admission):
    # Retain the original exact source/Backup/time/xid/LSN/marker validator.
    p.render(cluster, backup, custody, admission)
    status = backup["status"]
    timeline = custody["timeline"]
    first = int(status["beginWal"][8:16], 16) * 256 + int(status["beginWal"][16:], 16)
    last = (p.pitr.lsn(custody["markers"]["C"]["acknowledged_flush_lsn"]) - 1) // (16 * 1024**2)
    p.pitr.require(0 <= last - first < 64, "refuse unbounded or reversed native WAL retrieval range")
    names = [f"{timeline:08X}{i // 256:08X}{i % 256:08X}" for i in range(first, last + 1)]
    expected = {"backup_uid": backup["metadata"]["uid"], "backup_id": status["backupId"], "wal_names": names}
    labels = {
        "app.kubernetes.io/part-of": "verdify",
        "app.kubernetes.io/component": "cnpg-s2-archive-reader",
        "verdify.ai/qualification-target": p.SOURCE,
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "verdify-cnpg-s2-protected-archive-reader", "namespace": p.pitr.NS, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 180,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "containers": [
                        {
                            "name": "reader",
                            "image": IMAGE,
                            "command": ["python3", "-c", READER],
                            "env": [
                                {"name": "EXPECTED_ARCHIVE", "value": json.dumps(expected, sort_keys=True)},
                                {
                                    "name": "AWS_ACCESS_KEY_ID",
                                    "valueFrom": {
                                        "secretKeyRef": {
                                            "name": "verdify-cnpg-rehearsal-s3-reader",
                                            "key": "AWS_ACCESS_KEY_ID",
                                        }
                                    },
                                },
                                {
                                    "name": "AWS_SECRET_ACCESS_KEY",
                                    "valueFrom": {
                                        "secretKeyRef": {
                                            "name": "verdify-cnpg-rehearsal-s3-reader",
                                            "key": "AWS_SECRET_ACCESS_KEY",
                                        }
                                    },
                                },
                                {
                                    "name": "AWS_DEFAULT_REGION",
                                    "valueFrom": {
                                        "secretKeyRef": {
                                            "name": "verdify-cnpg-rehearsal-s3-region",
                                            "key": "AWS_DEFAULT_REGION",
                                        }
                                    },
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
    for name in ("cluster", "backup", "custody", "admission", "capture-directory", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    custody = json.loads(args.custody.read_text())
    p.pitr.validate_captures(custody, args.capture_directory)
    job = render(
        json.loads(args.cluster.read_text()),
        json.loads(args.backup.read_text()),
        custody,
        json.loads(args.admission.read_text()),
    )
    with args.output.open("x") as stream:
        yaml.safe_dump(job, stream, sort_keys=False)


if __name__ == "__main__":
    main()
