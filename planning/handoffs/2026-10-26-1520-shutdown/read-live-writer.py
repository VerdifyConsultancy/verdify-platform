import datetime,json,subprocess
from pathlib import Path

def kube(*args,input=None):
    return subprocess.check_output(['kubectl','--context','vallery',*args],input=input,text=True,timeout=30)
def obj(*args):
    return json.loads(kube(*args,'-o','json'))
app=obj('-n','argocd','get','application','verdify-prod-dark')
dep=obj('-n','verdify-prod','get','deployment','verdify-ingestor')
selector=','.join(k+'='+v for k,v in sorted(dep['spec']['selector']['matchLabels'].items()))
pods=obj('-n','verdify-prod','get','pods','-l',selector)['items']
assert len(pods)==1 and not pods[0]['metadata'].get('deletionTimestamp')
pod=pods[0]
node=obj('get','node','vm-k3s-node5')
code='''from pathlib import Path
import os,json,hashlib
root=Path(os.environ.get('STATE_DIR','/srv/verdify/state'))
out={'files':{},'existing_esp32_sockets':[]}
for name in ('writer-stage-state.json','writer-policy-hold.json'):
 p=root/name
 raw=p.read_bytes();o=json.loads(raw)
 x={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
 if name=='writer-stage-state.json':
  x.update({k:o.get(k) for k in ('status','halt_reason','run_id')})
  x['completed_count']=len(o.get('completed',[]));x['records_count']=len(o.get('records',[]))
 else:
  x.update({k:o.get(k) for k in ('reason','disposition','generation','source_revision')})
  x['field_count']=len(o.get('fields',[]))
 out['files'][name]=x
for f in ('/proc/net/tcp','/proc/net/tcp6'):
 for line in Path(f).read_text().splitlines()[1:]:
  c=line.split()
  if int(c[2].split(':')[1],16)==6053:out['existing_esp32_sockets'].append({'local_hex':c[1],'remote_hex':c[2],'state':c[3],'inode':c[9]})
print(json.dumps(out))
'''
inside=json.loads(kube('-n','verdify-prod','exec','-i',pod['metadata']['name'],'-c','ingestor','--','python','-',input=code))
r={'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'read_only':True,'argo':{'sync':app['status']['sync']['status'],'health':app['status']['health']['status'],'revision':app['status']['sync']['revision'],'operation':app.get('operation'),'reconciledAt':app['status'].get('reconciledAt')},'ingestor':{'name':pod['metadata']['name'],'uid':pod['metadata']['uid'],'node':pod['spec']['nodeName'],'phase':pod['status']['phase'],'containers':[{k:c.get(k) for k in ('name','ready','restartCount','imageID')} for c in pod['status'].get('containerStatuses',[])]},'node5':{'unschedulable':node['spec'].get('unschedulable',False),'ready':next(c['status'] for c in node['status']['conditions'] if c['type']=='Ready')},**inside}
Path('/private/tmp/verdify-resume-review-20261026/live-writer.json').write_text(json.dumps(r,indent=2)+'\n')
print(json.dumps(r,indent=2))
