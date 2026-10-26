import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone

out=Path('/private/tmp/verdify-resume-review-20261026')
name='verdify-finite-native-d7c25b0c-20261026-01'
expected_uid='88700998-b28a-4303-8363-3cb053dfbd00'
k=['kubectl','--context','vallery','--request-timeout=15s','-n','agent-fleet-ci']
def atomic(path,data):
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_bytes(data)
    os.replace(tmp,path)
def get(args):
    return json.loads(subprocess.check_output(k+args))
w=get(['get','workflow',name,'-o','json'])
assert w['metadata']['uid']==expected_uid
args={r['name']:r.get('value') for r in w['spec']['arguments']['parameters']}
assert args['revision']=='d7c25b0c620d537d56f938302d8be236a4d46fbb'
pods=get(['get','pods','-l','workflows.argoproj.io/workflow='+name,'-o','json'])['items']
s={'name':name,'uid':expected_uid,'captured_at':datetime.now(timezone.utc).isoformat(),'phase':w.get('status',{}).get('phase'),'message':w.get('status',{}).get('message'),'startedAt':w.get('status',{}).get('startedAt'),'finishedAt':w.get('status',{}).get('finishedAt'),'pods':[]}
for p in pods:
    podname=p['metadata']['name']
    main=next(c for c in p['spec']['containers'] if c['name']=='main')
    statuses=[{k:c.get(k) for k in ['name','imageID','restartCount','state']} for c in p.get('status',{}).get('containerStatuses',[])]
    row={'name':podname,'uid':p['metadata']['uid'],'node':p['spec'].get('nodeName'),'phase':p.get('status',{}).get('phase'),'main_resources':main.get('resources'),'statuses':statuses}
    s['pods'].append(row)
    if '-run-check-' not in podname:
        continue
    path=out/(name+'-check.log')
    tmp=path.with_suffix('.log.tmp')
    with tmp.open('wb') as f:
        result=subprocess.run(k+['logs',podname,'-c','main'],stdout=f,stderr=subprocess.PIPE)
    if result.returncode:
        tmp.unlink(missing_ok=True)
        row['log_error']=result.stderr.decode(errors='replace').strip()
        continue
    os.replace(tmp,path)
    data=path.read_bytes()
    row['log_bytes']=len(data)
    row['log_sha256']=hashlib.sha256(data).hexdigest()
    custody=out/'firmware1091-native-custody'
    custody.mkdir(exist_ok=True)
    parsed=0
    for line in data.decode(errors='replace').splitlines():
        if line.startswith('NATIVE_CUSTODY_FILE='):
            try:record=json.loads(line.split('=',1)[1])
            except json.JSONDecodeError:continue
            rel=Path(record['path'])
            assert not rel.is_absolute() and '..' not in rel.parts
            payload=base64.b64decode(record['data_b64'],validate=True)
            assert len(payload)==record['bytes'] and hashlib.sha256(payload).hexdigest()==record['sha256']
            dest=custody/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            atomic(dest,payload)
            parsed+=1
        elif line.startswith('NATIVE_CUSTODY_MANIFEST='):
            try:record=json.loads(line.split('=',1)[1])
            except json.JSONDecodeError:continue
            atomic(out/'firmware1091-native-custody-manifest.json',(json.dumps(record,indent=2)+'\n').encode())
        elif line.startswith('FIRMWARE_STRICT_RESULTS='):
            try:record=json.loads(line.split('=',1)[1])
            except json.JSONDecodeError:continue
            assert record['source']==args['revision']
            atomic(out/'firmware1091-native-results.json',(json.dumps(record,indent=2)+'\n').encode())
            row['results']=record
    row['custody_files_verified']=parsed
    lines=data.decode(errors='replace').splitlines()
    row['recent_output']=[x[:400] for x in lines[-10:] if not x.startswith(('NATIVE_CUSTODY_FILE=','NATIVE_CUSTODY_MANIFEST='))]
atomic(out/'firmware1091-native-live-status.json',(json.dumps(s,indent=2)+'\n').encode())
# Preserve completed workflow evidence, excluding the large unchanged command argument.
if s['phase'] in ['Succeeded','Failed','Error']:
    terminal={'metadata':{key:w['metadata'].get(key) for key in ['name','uid','creationTimestamp']},'source':args['revision'],'base':args.get('base_revision'),'status':w.get('status',{})}
    atomic(out/'firmware1091-native-terminal-workflow.json',(json.dumps(terminal,indent=2)+'\n').encode())
print(json.dumps(s,indent=2))
