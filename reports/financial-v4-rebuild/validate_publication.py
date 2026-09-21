"""REPRODUCTION_EVIDENCE: published financial rows, no Snapshot initialization."""
import itertools,json,time
from collections import Counter
from pathlib import Path
from axiom_data.artifacts import _load_manifest
from axiom_data.layout import DataRootLayout
from axiom_data.partition_rows import PartitionRows
from axiom_data.pit import select_revisions,usable_from
from axiom_data.domains.market import MarketContractError
P=Path(__file__).resolve().parent;ROOT=Path('/var/lib/axiom-data')
result=json.loads((P/'publication-result.json').read_bytes());identity=result['commit_id'];start=time.monotonic()
layout=DataRootLayout(ROOT);path=layout.domain_commits('financial_events')/identity
manifest,digest=_load_manifest(ROOT,path,artifact_type='domain_commit',schema_version=('domain_commit.v1','domain_commit.v2'),identity_field='domain_commit_id',identity=identity)
contract=json.loads((path/'contract.json').read_bytes());assert contract['contract_version']=='financial_events.v4'
rows=PartitionRows(layout,'financial_events',manifest,contract)
expected={x['key']:x for x in json.loads((P/'real-v4-keys.json').read_bytes())};seen=set();counts=Counter();actual_example=[];row_count=0
for entry in rows.entries:
 for key,values in itertools.groupby(rows._rows(entry),key=lambda r:r['logical_event_key']):
  group=list(values);row_count+=len(group)
  if (group[0]['symbol'],group[0]['report_period'],group[0]['endpoint'])==('002010.SZ','2026-06-30','fina_indicator'):
   for policy in ('operational_pit_v1','best_effort_vendor_v1'):
    winner=select_revisions(group,policy=policy,knowledge_cutoff='2026-09-21T23:59:59+08:00')
    assert len(winner)==1 and winner[0]['values']['roe']==0.024916
    actual_example.append({'policy':policy,'roe':winner[0]['values']['roe'],'revision_id':winner[0]['revision_id'],'source_ref':winner[0]['source_ref']})
  if key not in expected:continue
  assert key not in seen;seen.add(key)
  for policy in ('operational_pit_v1','best_effort_vendor_v1'):
   dates={usable_from(dict(row,first_observed_at=o['observed_at'],vendor_available_at=o['vendor_available_at']),policy) for row in group for o in row['observations']}
   failed=False
   for cutoff in sorted(d for d in dates if d is not None):
    try:select_revisions(group,policy=policy,knowledge_cutoff=cutoff)
    except MarketContractError as exc:
     assert 'ambiguous simultaneous revisions' in str(exc);failed=True
   assert failed==expected[key]['conflicts'][policy],(key,policy)
   counts[policy+('_ambiguous' if failed else '_orderable')]+=1
assert row_count==len(rows) and len(seen)==1952 and len(actual_example)==2
out={'status':'PASS','commit':identity,'rows':row_count,'partitions':len(rows.entries),'known_keys_checked':len(seen),'counts':dict(counts),'actual_002010':actual_example,'elapsed':time.monotonic()-start,'scope':'all published financial partition integrity; known ambiguity keys and original indicator regression; no Snapshot/Views'}
(P/'publication-validation.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out),flush=True)
