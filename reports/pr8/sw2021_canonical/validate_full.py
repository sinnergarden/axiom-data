"""Read-only canonical projection validation over explicitly frozen inputs."""
import json
from collections import Counter
from pathlib import Path
from axiom_data import load_domain_commit,load_raw_batch,validate_domain_commit_closure
from axiom_data.pit import select_revisions
from axiom_data.sw_industry import project_state

ROOT=Path('/home/liuming/workspace/axiom/data')
RUN=ROOT/'operations/sw2021-canonical-20260910-r1'
ref=json.loads((RUN/'industry_result.json').read_bytes())
scope=json.loads((ROOT/'operations/industry-qualification-20260909-r1/scope.json').read_bytes())
industry=load_domain_commit(ROOT,'industry_membership',ref['commit_id'])
validate_domain_commit_closure(ROOT,'industry_membership',ref['commit_id'])
master=load_domain_commit(ROOT,'security_master',ref['security_master_commit_id'])
security={r['symbol']:r for r in master.rows}
states=select_revisions(industry.rows,policy='best_effort_vendor_v1',knowledge_cutoff='2026-09-10T00:00:00+08:00')
sessions={}
for identity in scope['calendar_raw_batch_ids']:
    raw=load_raw_batch(scope['calendar_root'],identity)
    exchange=raw.manifest['request']['params']['exchange']
    sessions[exchange]=sorted(r['cal_date'][:4]+'-'+r['cal_date'][4:6]+'-'+r['cal_date'][6:]
        for r in json.loads(raw.payload) if r['is_open']==1)
counts=Counter(); steel=Counter(); gaps={}
assert set(security)=={r['symbol'] for r in states}==set(scope['symbols'])
for state in states:
    symbol=state['symbol'];s=security[symbol];local=Counter()
    for day in sessions[s['exchange']]:
        if not scope['start']<=day<=scope['end']:continue
        fact=project_state(state,s,day)
        counts[fact['availability_state']]+=1;local[fact['availability_state']]+=1
        if fact['industry_id']=='850412.SI':
            assert fact['mapping_provenance']['source_code']=='850401.SI'
            assert fact['availability_state']=='classified'
            steel[symbol]+=1
        assert fact.get('missing_reason')!='taxonomy_code_unresolved'
    if local['classification_unavailable']:gaps[symbol]=local['classification_unavailable']
assert sum(steel.values())==32458,steel
assert counts['classified']==9035545,counts
assert counts['classification_unavailable']==125648,counts
assert counts['boundary_session_ambiguous']==1180,counts
assert len(gaps)==110,gaps
print(json.dumps(dict(status='PASS',commit_id=ref['commit_id'],security_commit_id=ref['security_master_commit_id'],
    target_securities=len(states),scope={'start':scope['start'],'end':scope['end']},counts=dict(counts),
    restored_steel_sessions=sum(steel.values()),steel_by_security=dict(steel),gaps_by_security=gaps),ensure_ascii=False,indent=2))
