import datetime,json,subprocess
from pathlib import Path
P=Path('/private/tmp/verdify-resume-review-20261026')
def run(args,stdin=None):return subprocess.check_output(args,input=stdin,text=True,timeout=45)
def k(*a,stdin=None):return run(['kubectl','--context','vallery','--request-timeout=20s',*a],stdin)
def obj(*a):return json.loads(k(*a,'-o','json'))
now=lambda:datetime.datetime.now(datetime.timezone.utc).isoformat()
work=obj('-n','verdify-prod','get','deploy,sts,cronjob,pods')
rows=[]
for r in work['items']:
 d={'kind':r['kind'],'name':r['metadata']['name'],'uid':r['metadata']['uid']}
 if r['kind'] in ('Deployment','StatefulSet'): d.update(desired=r['spec'].get('replicas'),ready=r['status'].get('readyReplicas',0),updated=r['status'].get('updatedReplicas',0))
 if r['kind']=='CronJob': d.update(suspend=r['spec'].get('suspend',False),active=r['status'].get('active',[]),lastSuccessfulTime=r['status'].get('lastSuccessfulTime'))
 if r['kind']=='Pod':d.update(node=r['spec'].get('nodeName'),phase=r['status'].get('phase'),containers=[{x:c.get(x) for x in ('name','ready','restartCount','imageID')} for c in r['status'].get('containerStatuses',[])])
 rows.append(d)
(P/'shutdown-workloads.json').write_text(json.dumps({'captured_at':now(),'resources':rows},indent=2)+'\n')
node=obj('get','nodes')
(P/'shutdown-nodes.json').write_text(json.dumps({'captured_at':now(),'nodes':[{'name':n['metadata']['name'],'unschedulable':n['spec'].get('unschedulable',False),'conditions':[{x:c.get(x) for x in ('type','status','reason')} for c in n['status']['conditions'] if c['type'] in ['Ready','MemoryPressure','DiskPressure','PIDPressure']]} for n in node['items']]},indent=2)+'\n')
wf=obj('-n','agent-fleet-ci','get','workflows')
rows=[]
for w in wf['items']:
 params={x['name']:x.get('value') for x in w.get('spec',{}).get('arguments',{}).get('parameters',[]) if x['name'] in ['repo','revision','base_revision','commit_sha','source_sha','git_revision']}
 if 'verdify' not in w['metadata']['name'] and 'VerdifyConsultancy/verdify-platform' not in params.values():continue
 rows.append({'name':w['metadata']['name'],'uid':w['metadata']['uid'],'createdAt':w['metadata']['creationTimestamp'],'phase':w.get('status',{}).get('phase'),'startedAt':w.get('status',{}).get('startedAt'),'finishedAt':w.get('status',{}).get('finishedAt'),'message':w.get('status',{}).get('message'),'template':w['spec'].get('workflowTemplateRef'),'parameters':params,'activeDeadlineSeconds':w['spec'].get('activeDeadlineSeconds'),'node_summary':[{'name':n.get('displayName'),'phase':n.get('phase'),'message':n.get('message')} for n in w.get('status',{}).get('nodes',{}).values()]})
(P/'shutdown-workflows.json').write_text(json.dumps({'captured_at':now(),'workflows':rows},indent=2)+'\n')
query="""BEGIN READ ONLY; SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s'; SELECT jsonb_build_object('captured_at',clock_timestamp(),'read_only',current_setting('transaction_read_only'),'climate_latest',(SELECT max(ts) FROM climate WHERE ts>now()-interval '1 hour'),'action_latest',(SELECT max(ts) FROM climate_action_log WHERE ts>now()-interval '1 hour'),'setpoint_snapshot_latest',(SELECT max(ts) FROM setpoint_snapshot WHERE ts>now()-interval '1 hour'),'firmware',(SELECT jsonb_build_object('ts',ts,'version',firmware_version) FROM diagnostics WHERE ts>now()-interval '1 hour' AND firmware_version IS NOT NULL ORDER BY ts DESC LIMIT 1)); ROLLBACK;"""
raw=k('-n','verdify-prod','exec','-i','verdify-db-0','-c','postgres','--','psql','-U','verdify','-d','verdify','-X','-q','-t','-A','-v','ON_ERROR_STOP=1',stdin=query)
(P/'shutdown-telemetry.json').write_text(json.dumps(json.loads(raw.strip()),indent=2)+'\n')
api=next(r for r in work['items'] if r['kind']=='Pod' and r['metadata']['name'].startswith('verdify-api-') and r['status'].get('phase')=='Running')
code="""import json,urllib.request
out={}
for path in ['/health','/health/detailed']:
 with urllib.request.urlopen('http://127.0.0.1:8000'+path,timeout=10) as r:
  d=json.load(r);out[path]={'http_status':r.status,'status':d.get('status'),'git_sha':d.get('git_sha'),'checks':{k:v for k,v in d.get('checks',{}).items() if k in ['db_reachable','climate_age_seconds','climate_action_log_age_seconds','active_alerts_1h','compliance_pct']}}
print(json.dumps(out))
"""
# Resolve the serving port from the actual API container's declared port.
c=next(c for c in api['spec']['containers'] if c['name']=='api')
port=next((p['containerPort'] for p in c.get('ports',[]) if p.get('name')=='http'),8000)
code=code.replace('127.0.0.1:8000','127.0.0.1:'+str(port))
try:
 health=json.loads(k('-n','verdify-prod','exec','-i',api['metadata']['name'],'-c','api','--','python','-',stdin=code))
except Exception as e:health={'probe_error':str(e)}
(P/'shutdown-api-health.json').write_text(json.dumps({'captured_at':now(),'pod':api['metadata']['name'],'endpoints':health},indent=2)+'\n')
print(json.dumps({'captured_at':now(),'resources':len(work['items']),'workflows':[{'name':w['name'],'phase':w['phase']} for w in rows if w['phase'] not in ['Succeeded','Failed','Error']],'telemetry':json.loads(raw.strip()),'health':health},indent=2))
