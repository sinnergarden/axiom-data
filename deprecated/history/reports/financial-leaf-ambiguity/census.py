"""REPRODUCTION_EVIDENCE: full immutable financial census versus strict PIT selection."""
import itertools,json,time
from collections import Counter
from pathlib import Path
from axiom_data.artifacts import _load_manifest,_validate_manifest_identity,_digest
from axiom_data.layout import DataRootLayout
from axiom_data.partition_rows import PartitionRows
from axiom_data.pit import (select_revisions,select_financial_revisions,financial_ambiguities,
    financial_derived,usable_from,instant,AMBIGUOUS_SOURCE_REVISION)
from axiom_data.domains.market import MarketContractError
ROOT=Path('/var/lib/axiom-data');P=Path(__file__).resolve().parent
ID='financial_events-7fe86eee9a5e0b64e9fb8cc47c0446a6677c4d4f0de59bef6979244e4d15c04a'
POLICIES=('operational_pit_v1','best_effort_vendor_v1','market_pit_safe_v1')
start=time.monotonic();layout=DataRootLayout(ROOT);path=layout.domain_commits('financial_events')/ID
manifest,digest=_load_manifest(ROOT,path,artifact_type='domain_commit',schema_version='domain_commit.v2',identity_field='domain_commit_id',identity=ID)
_validate_manifest_identity(manifest,'domain_commit_id','financial_events',ID)
cbytes=(path/'contract.json').read_bytes();assert _digest(cbytes)==manifest['contract_digest']
partitions=PartitionRows(layout,'financial_events',manifest,json.loads(cbytes))
counts=Counter();conflicts=[];keys={p:set() for p in POLICIES}
for entry in partitions.entries:
 for key,iterator in itertools.groupby(partitions._rows(entry),key=lambda r:r['logical_event_key']):
  rows=list(iterator);counts['rows']+=len(rows);counts['logical_keys']+=1
  if len({r['revision_id'] for r in rows})<2:continue
  counts['multiple_revision_keys']+=1
  for policy in POLICIES:
   candidates=[]
   for row in rows:
    for obs in row['observations']:
     usable=usable_from(dict(row,first_observed_at=obs['observed_at'],vendor_available_at=obs['vendor_available_at']),policy)
     if usable is not None:candidates.append((instant(usable),instant(obs['observed_at']),row,obs))
   times=sorted({u for u,_,_,_ in candidates});expected_intervals=[]
   for i,cutoff in enumerate(times):
    counts[policy+'_transitions']+=1
    resolved=select_financial_revisions(rows,policy=policy,knowledge_cutoff=cutoff.isoformat())
    try:strict=select_revisions(rows,policy=policy,knowledge_cutoff=cutoff.isoformat())
    except MarketContractError as exc:
     assert 'ambiguous simultaneous revisions' in str(exc)
    else:
     assert strict==resolved,(key,policy,'non-conflict drift')
     continue
    keys[policy].add(key);counts[policy+'_conflict_intervals']+=1
    visible=[c for c in candidates if c[0]<=cutoff];rank=max(c[:2] for c in visible)
    tied=[(r,o) for u,t,r,o in visible if (u,t)==rank]
    fields=set().union(*(r['values'] for r,o in tied))
    differing=sorted(f for f in fields if len({json.dumps(r['values'].get(f),sort_keys=True) for r,o in tied})>1)
    assert len(resolved)==1;r=resolved[0]
    assert r['ambiguous_fields']==differing and r['revision_id'] is None and r['source_ref'] is None
    assert {x['revision_id'] for x in r['component_revisions']}=={v['revision_id'] for v,o in tied}
    for field in fields:
     if field in differing:
      assert r['values'][field] is None and r['missing_reasons'][field]==AMBIGUOUS_SOURCE_REVISION
      counts[policy+'_unavailable_leaves']+=1
     else:assert r['values'][field]==tied[0][0]['values'].get(field)
    event={'logical_event_key':key,'symbol':r['symbol'],'endpoint':r['endpoint'],'report_period':r['report_period'],
           'policy':policy,'from':cutoff.isoformat(),'to_exclusive':times[i+1].isoformat() if i+1<len(times) else None,
           'ambiguous_fields':differing,'revision_refs':sorted({v['revision_id'] for v,o in tied})}
    expected_intervals.append((event['from'],event['to_exclusive'],differing))
    conflicts.append(event)
    if r['endpoint']=='income':
     derived=financial_derived(rows,policy=policy,knowledge_cutoff=cutoff.isoformat())
     for d in derived:
      field=d['field'].removeprefix('single_quarter_').removeprefix('ttm_')
      if field in differing:
       assert d['value'] is None and d['missing_reason']==AMBIGUOUS_SOURCE_REVISION
   if times:
    actual=financial_ambiguities(rows,policy=policy,knowledge_cutoff=times[-1].isoformat())
    assert [(r['from'],r['to_exclusive'],r['ambiguous_fields']) for r in actual]==expected_intervals
 counts['partitions']+=1
assert counts['rows']==len(partitions)
known=json.loads((Path(__file__).resolve().parents[2]/'reports/financial-v4-rebuild/real-v4-keys.json').read_bytes())
for policy in POLICIES[:2]:
 assert keys[policy]=={r['key'] for r in known if r['conflicts'][policy]},policy
assert len(keys['operational_pit_v1'])==1912 and len(keys['best_effort_vendor_v1'])==16
result={'status':'PASS','commit':ID,'manifest_digest':digest,'counts':dict(counts),
        'ambiguity_keys':{p:len(v) for p,v in keys.items()},'elapsed_seconds':time.monotonic()-start,
        'canonical_mutation':False,'strict_nonconflicting_results_equal':True,'all_conflict_intervals_recorded':True}
(P/'census-result.json').write_text(json.dumps(result,indent=2)+'\n')
(P/'census-intervals.json').write_text('[\n'+',\n'.join(json.dumps(r,separators=(',',':')) for r in conflicts)+'\n]\n')
print(json.dumps(result),flush=True)
