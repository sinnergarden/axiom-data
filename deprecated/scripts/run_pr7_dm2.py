"""Rebuild a bounded D-M2 closure using only explicit frozen raw identities."""
import argparse
import json
from pathlib import Path
from axiom_data import BuildApplication,create_snapshot,SnapshotReader
from axiom_data.artifacts import _DOMAIN_DEPENDENCIES
from axiom_data.domains import PR7_DOMAINS
from axiom_data.pr7_source import Pr7Builder
from axiom_data.pr7_views import build_pr7_fact_view
from run_pr6_pit_financial import prepare_inputs,build as build_pr6,copy_raw

RUN=Path('/home/liuming/workspace/axiom/data/forensic/pr7-dm2-20260909-r1')

def prepare(target,run):
    plan=prepare_inputs(target)
    registry=json.loads((run/'collection_registry.json').read_text())
    plan['pr7_raw']={d:[] for d in PR7_DOMAINS}
    for ref in registry['raw_batches'].values():
        raw=copy_raw(run/'raw-root',target,ref)
        plan['pr7_raw'][raw.manifest['domain']].append(ref)
    for refs in plan['pr7_raw'].values():refs.sort()
    return plan


def build(target,plan):
    result=build_pr6(target,plan);commits=result['domain_commit_ids']
    for domain in PR7_DOMAINS:
        b=Pr7Builder(target,domain,dependency_commit_ids={d:commits[d] for d in _DOMAIN_DEPENDENCIES[domain]})
        commits[domain]=BuildApplication(domain,b).build(None,plan['pr7_raw'][domain],[],domain+'.v1').commit_id
    snapshot=create_snapshot(target,commits)
    reader=SnapshotReader(target,snapshot.snapshot_id)
    view=build_pr7_fact_view(target,snapshot.snapshot_id,symbols=['600036.SH','688981.SH','000401.SZ'],start_session='2025-06-10',end_session='2025-06-13',pit_policy='best_effort_vendor_v1',knowledge_cutoff='2025-06-13T23:59:59+08:00')
    from axiom_data.pr6_views import build_pr6_fact_view
    from axiom_data.views import build_adjusted_price_view
    pr6=build_pr6_fact_view(target,snapshot.snapshot_id,symbols=['600036.SH','688981.SH'],start_session='2025-06-10',end_session='2025-06-13',universe_ids=['000906.SH','000852.SH'],industry_system='tushare_bak_basic',pit_policy='best_effort_vendor_v1',knowledge_cutoff='2025-06-13T23:59:59+08:00')
    adjusted=build_adjusted_price_view(target,snapshot.snapshot_id,symbols=['600036.SH','688981.SH'],start_session='2025-06-10',end_session='2025-06-13',anchor_session='2025-06-13',pit_policy='research_non_pit',decision_cutoff='2025-06-13')
    return {'pr6_view_id':pr6.view_id,'adjusted_view_id':adjusted.view_id,'snapshot_id':snapshot.snapshot_id,'domain_commit_ids':commits,'pr7_view_id':view.view_id,
        'rows':{d:len(c.rows) for d,c in reader.commits.items()}}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-root',type=Path,default=RUN);p.add_argument('--target',type=Path,required=True);p.add_argument('--plan',type=Path)
    a=p.parse_args();plan=json.loads(a.plan.read_text()) if a.plan else prepare(a.target,a.run_root)
    if not a.plan:(a.run_root/'build_plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    result=build(a.target,plan);(a.target/'run_result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
