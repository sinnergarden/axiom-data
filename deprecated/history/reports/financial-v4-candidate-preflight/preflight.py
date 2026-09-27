"""REPRODUCTION_EVIDENCE: read-only v4 financial View prerequisite counterexample."""
import json,time,hashlib
from pathlib import Path
from axiom_data.artifacts import _load_manifest,_validate_manifest_identity,_digest,load_raw_batch
from axiom_data.partition_rows import PartitionRows
from axiom_data.layout import DataRootLayout
from axiom_data.pit import select_revisions
from axiom_data.domains.market import MarketContractError
ROOT=Path('/var/lib/axiom-data');OUT=Path(__file__).resolve().parent;start=time.monotonic()
OLD='snapshot-aa48fb719f5db092e81a24b188e469e38d3ef05bde77f4ec39282e55b5ebb650'
FIN='financial_events-7fe86eee9a5e0b64e9fb8cc47c0446a6677c4d4f0de59bef6979244e4d15c04a'
def load(path,kind,schemas,field,identity,prefix):
 m,d=_load_manifest(ROOT,path,artifact_type=kind,schema_version=schemas,identity_field=field,identity=identity)
 _validate_manifest_identity(m,field,prefix,identity)
 return m,d
old,sd=load(ROOT/'snapshots'/OLD,'data_snapshot','data_snapshot.v4','snapshot_id',OLD,'snapshot')
m,md=load(ROOT/'canonical/financial_events/commits'/FIN,'domain_commit',('domain_commit.v1','domain_commit.v2'),'domain_commit_id',FIN,'financial_events')
c=(ROOT/'canonical/financial_events/commits'/FIN/m['contract_path']).read_bytes();assert _digest(c)==m['contract_digest']
assert json.loads(c)['contract_version']=='financial_events.v4'
plan=json.loads((ROOT/'operations/v1-full-bootstrap-20260913-r1/required-views.json').read_bytes())
request=next(v for v in plan.values() if v['kind']=='pr6_fact' and v['config']['symbols']==['688981.SH'])
policy=request['config']['pit_policy'];cutoff=request['config']['knowledge_cutoff']
parts=PartitionRows(DataRootLayout(ROOT),'financial_events',m,json.loads(c))
entry=next(e for e in parts.entries if e['key']=='balancesheet-2015')
rows=[r for r in parts._rows(entry) if r['symbol']=='002961.SZ' and r['report_period']=='2015-12-31' and r['report_type']=='1']
assert len(rows)>1 and len({r['logical_event_key'] for r in rows})==1
try:select_revisions(rows,policy=policy,knowledge_cutoff=cutoff)
except MarketContractError as exc:
 assert 'ambiguous simultaneous revisions' in str(exc)
 failure={'exception':type(exc).__name__,'message':str(exc)}
else:raise AssertionError('prerequisite is now orderable; continue requested operation')
raws=[]
for ref in sorted({r['source_ref'] for r in rows}):
 raw=load_raw_batch(ROOT,ref);payload=json.loads(raw.payload)
 matches=[{'index':i,'row':r} for i,r in enumerate(payload) if r.get('ts_code')=='002961.SZ' and r.get('end_date')=='20151231' and str(r.get('report_type'))=='1']
 raws.append({'raw_batch_id':ref,'manifest_digest':raw.ref.manifest_digest,'matches':matches})
result={'status':'BLOCKED_BEFORE_PUBLICATION','base':'21382552bfa9531ad2c93c55a7beeeb7d6e66b33','old_snapshot':OLD,'old_snapshot_manifest_digest':sd,'proposed_financial_commit':FIN,'financial_manifest_digest':md,'proposed_unchanged_refs':{d:r for d,r in old['domain_refs'].items() if d!='financial_events'},'request':request,'policy':policy,'cutoff':cutoff,'partition':entry,'canonical_rows':rows,'raw_evidence':raws,'failure':failure,'elapsed_seconds':time.monotonic()-start,'validation_scope':'manifest identity/digest, contract digest, complete single partition integrity and exact PIT logical key; not full Snapshot closure or View execution','candidate_id':None,'formal_writes':False}
assert len(result['proposed_unchanged_refs'])==17
(OUT/'preflight-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ('status','policy','cutoff','failure','elapsed_seconds','candidate_id','formal_writes')}),flush=True)
