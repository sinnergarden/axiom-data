"""REPRODUCTION_EVIDENCE: executed financial-only public rebuild from frozen Raw refs.
No supplier, Snapshot, View, catalog or pointer writes. Run after a clean commit.
"""
import fcntl,hashlib,json,os,socket,subprocess,time,traceback
from pathlib import Path
from unittest.mock import patch
from axiom_data import artifacts,BuildApplication
from axiom_data.pr6_source import Pr6Builder
P=Path(__file__).resolve().parent
ROOT=Path('/var/lib/axiom-data')
OLD='financial_events-ff83e5efc1f4c805281c556030b219de225fc2dba16e3a16ccf57cab6d8b17a7'
RUN='financial-v4-rebuild-20260921-r1'
START=time.monotonic()
def save(name,value):
 tmp=P/(name+'.pending');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(P/name)
def event(stage,**kw):
 data=dict(run_id=RUN,stage=stage,pid=os.getpid(),elapsed=time.monotonic()-START,**kw)
 save('publication-progress.json',data);print(json.dumps(data),flush=True)
def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as stream:
  for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def state():
 return {'commits':sorted(str(p.relative_to(ROOT)) for p in (ROOT/'canonical').glob('*/commits/*')),
 'snapshots':sorted(str(p.relative_to(ROOT)) for p in (ROOT/'snapshots').glob('*')),
 'views':sorted(str(p.relative_to(ROOT)) for p in (ROOT/'derived').glob('*/commits/*')),
 'pointers_catalog':{str(p.relative_to(ROOT)):digest(p) for name in ('current.json','default.json','catalog.sqlite') if (p:=ROOT/name).is_file()}}
def no_network(*a,**kw):raise RuntimeError('network forbidden')
def main():
 lock=(P/'publication.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 assert not (P/'publication-result.json').exists(),'existing result must be validated, not rebuilt'
 code=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
 assert not subprocess.check_output(['git','status','--porcelain'],text=True).strip(),'dirty code'
 oldpath=ROOT/'canonical/financial_events/commits'/OLD/'manifest.json'
 old_digest=digest(oldpath)
 old=json.loads(oldpath.read_bytes())
 ids=[r['raw_batch_id'] for r in old['ordered_raw_batch_refs']]
 assert len(ids)==230464 and len(ids)==len(set(ids))
 config={k:old['builder_config'][k] for k in ('symbols','start_session','end_session','storage_policy') if k in old['builder_config']}
 deps={k:v['domain_commit_id'] for k,v in old['dependency_commit_refs'].items()}
 assert set(deps)=={'security_master'}
 before=state();save('publication-before.json',before)
 save('publication-inputs.json',dict(run_id=RUN,code=code,old_commit=OLD,old_manifest_sha256=old_digest,raw_count=len(ids),raw_ids_sha256=artifacts._digest(artifacts._json_bytes(ids)),config=config,dependencies=deps,contract='financial_events.v4',parent=None))
 del old
 original=artifacts.load_raw_batch;loads=0
 def load(*args,**kwargs):
  nonlocal loads
  result=original(*args,**kwargs);loads+=1
  if loads%10000==0:event('PUBLIC_BUILD',raw_loads=loads)
  return result
 original_node=artifacts._validate_domain_commit_node
 def node(*args,**kwargs):
  event('CLOSURE_BEGIN',domain=args[1],commit=args[2],raw_loads=loads)
  result=original_node(*args,**kwargs)
  event('CLOSURE_PASS',domain=args[1],commit=args[2],raw_loads=loads)
  return result
 event('PUBLIC_BUILD_BEGIN',code=code,raw_count=len(ids))
 with patch.object(socket.socket,'connect',no_network),patch.object(socket.socket,'connect_ex',no_network),patch.object(artifacts,'load_raw_batch',load),patch.object(artifacts,'_validate_domain_commit_node',node):
  ref=BuildApplication('financial_events',Pr6Builder(ROOT,'financial_events',builder_config=config,dependency_commit_ids=deps)).build(None,ids,[],'financial_events.v4')
 # The public builder returns only after complete domain contract/Raw closure validation.
 path=ROOT/'canonical/financial_events/commits'/ref.commit_id/'manifest.json'
 new=json.loads(path.read_bytes())
 assert new['contract_version']=='financial_events.v4' and not new.get('parent_commit_ref')
 assert [r['raw_batch_id'] for r in new['ordered_raw_batch_refs']]==ids
 assert {k:v['domain_commit_id'] for k,v in new['dependency_commit_refs'].items()}==deps
 assert digest(oldpath)==old_digest
 after=state();save('publication-after.json',after)
 assert set(after['commits'])-set(before['commits'])=={str(path.parent.relative_to(ROOT))}
 assert set(before['commits'])<=set(after['commits'])
 for name in ('snapshots','views','pointers_catalog'):assert before[name]==after[name],name
 result=dict(run_id=RUN,code=code,commit_id=ref.commit_id,manifest_sha256=digest(path),contract=new['contract_version'],raw_count=len(ids),raw_loads=loads,partitions=len(new['partitions']),logical_content_digest=new['logical_content_digest'],elapsed=time.monotonic()-START,full_public_closure='PASS',old_manifest_unchanged=True,other_commits_snapshots_views_catalog_pointers_unchanged=True)
 save('publication-result.json',result);event('PUBLICATION_VALIDATED',**{k:v for k,v in result.items() if k not in ('run_id','elapsed')})
if __name__=='__main__':
 try:main()
 except Exception:
  event('FAILED');traceback.print_exc();raise
