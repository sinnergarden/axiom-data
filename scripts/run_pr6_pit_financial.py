#!/usr/bin/env python3
"""Reconstruct the bounded PR6 slice from explicit frozen raw and baseline identities."""
from __future__ import annotations
import argparse
import json
import shutil
from pathlib import Path
from axiom_data import (BuildApplication, TushareMarketBuilder, TushareDm1Builder,
    validate_domain_commit_closure, create_snapshot, load_raw_batch, SnapshotReader)
from axiom_data.artifacts import _DOMAIN_DEPENDENCIES
from axiom_data.domains import DM1_SNAPSHOT_DOMAINS, PR6_DOMAINS
from axiom_data.pr6_source import Pr6Builder

BASELINE = 'snapshot-b1303edb81a3e7b48f38909ebd52ae4e5512030679a3eadfb8ace7c9c91de842'
BASE_ROOT = Path('/home/liuming/workspace/axiom/data/forensic/pr5-dm1-market-reference-20260906-b1b6-r4')
RUN_ROOT = Path('/home/liuming/workspace/axiom/data/forensic/pr6-pit-financial-20260908-r1')
MEMBERSHIP_SYMBOLS=['000001.SZ','000062.SZ','000401.SZ','301581.SZ','600000.SH','600036.SH','603072.SH','688981.SH']


def copy_raw(source, target, identity):
    raw=load_raw_batch(source,identity)
    source_path=source/'raw'/'batches'/identity
    target_path=target/'raw'/'batches'/identity
    if target_path.exists():
        if load_raw_batch(target,identity).ref!=raw.ref:
            raise ValueError('existing raw differs')
    else:
        shutil.copytree(source_path,target_path)
    return raw


def prepare_inputs(target):
    baseline=SnapshotReader(BASE_ROOT,BASELINE)
    registry=json.loads((RUN_ROOT/'collection_registry.json').read_text())
    plan={'baseline_snapshot':BASELINE,'baseline_domains':{},'pr6_raw':{d:[] for d in PR6_DOMAINS},
          'membership_symbols':MEMBERSHIP_SYMBOLS}
    for domain in DM1_SNAPSHOT_DOMAINS:
        commit=baseline.commits[domain]
        if commit.manifest['parent_commit_ref'] is not None:
            raise ValueError('bounded baseline requires explicit parent reconstruction')
        refs=[r['raw_batch_id'] for r in commit.manifest['ordered_raw_batch_refs']]
        for ref in refs:copy_raw(BASE_ROOT,target,ref)
        config=dict(commit.manifest['builder_config'])
        if domain=='security_master':config['symbols']=MEMBERSHIP_SYMBOLS
        plan['baseline_domains'][domain]={'raw_batch_ids':refs,'config':config}
    for ref in registry['raw_batches'].values():
        raw=copy_raw(RUN_ROOT/'data-root',target,ref)
        plan['pr6_raw'][raw.manifest['domain']].append(ref)
    for refs in plan['pr6_raw'].values():refs.sort()
    return plan


def build(target,plan):
    commits={}
    for domain in DM1_SNAPSHOT_DOMAINS:
        spec=plan['baseline_domains'][domain]
        deps={name:commits[name] for name in _DOMAIN_DEPENDENCIES[domain]}
        if domain in ('trading_calendar','security_master','market_daily'):
            kwargs={}
            if domain=='market_daily':
                kwargs={'calendar_commit_id':deps['trading_calendar'],'security_master_commit_id':deps['security_master']}
            builder=TushareMarketBuilder(target,domain,builder_config=spec['config'],**kwargs)
        else:
            builder=TushareDm1Builder(target,domain,builder_config=spec['config'],dependency_commit_ids=deps)
        commits[domain]=BuildApplication(domain,builder).build(None,spec['raw_batch_ids'],[],domain+'.v1').commit_id
    for domain in PR6_DOMAINS:
        config={'membership_end_exclusive':'2025-07-01','symbols':plan['membership_symbols']} if domain=='universe_membership' else {}
        builder=Pr6Builder(target,domain,builder_config=config,dependency_commit_ids={d:commits[d] for d in _DOMAIN_DEPENDENCIES[domain]})
        commits[domain]=BuildApplication(domain,builder).build(None,plan['pr6_raw'][domain],[],domain+('.v3' if domain=='universe_membership' else '.v2')).commit_id
    snapshot=create_snapshot(target,commits)
    return {'snapshot_id':snapshot.snapshot_id,'domain_commit_ids':commits}


def validate_run(source, recovery, plan, report_dir):
    import socket
    from unittest.mock import patch
    from collections import Counter
    from axiom_data.pr6_views import build_pr6_fact_view
    from axiom_data.pr6_reconciliation import reconcile
    from axiom_data.evidence import pr6_actual_refs, validate_pr6_evidence
    from axiom_data import rebuild_catalog
    if recovery.exists():
        raise ValueError('recovery root already exists; validate or select an explicit new run identity')
    shutil.copytree(source/'raw',recovery/'raw')
    shutil.copytree(source/'evidence',recovery/'evidence')
    def denied(*args,**kwargs):
        raise RuntimeError('NETWORK_DISABLED_FOR_RECOVERY')
    def artifacts(root):
        result=build(root,plan)
        view=build_pr6_fact_view(root,result['snapshot_id'],symbols=['600036.SH','688981.SH'],
            start_session='2025-06-10',end_session='2025-06-13',
            universe_ids=['000906.SH','000852.SH'],industry_system='tushare_bak_basic',
            pit_policy='best_effort_vendor_v1',knowledge_cutoff='2025-06-13T23:59:59+08:00')
        rebuild_catalog(root)
        return pr6_actual_refs(root,result['snapshot_id'],view.view_id)
    # All reconstruction runs after collection, with network access disabled at
    # the Python transport boundary used by the supplier clients.
    with patch.object(socket,'socket',denied),patch.object(socket,'create_connection',denied):
        source_refs=artifacts(source)
        offline_refs=artifacts(recovery)
        report=reconcile(source,source_refs['snapshot']['snapshot_id'],source_refs['view']['view_id'])
    evidence={'schema_version':'pr6_evidence.v1','data_root':str(source),'offline_root':str(recovery),
        'artifact_refs':source_refs,'offline_artifact_refs':offline_refs,'reconciliation':report,
        'gates':{'artifact_closure':True,'offline_identity_and_values':source_refs==offline_refs,
                 'historical_union_exact':report['historical_union']['count']==3601,
                 'direct_qlib_values':report['direct_qlib_comparisons']>0,
                 'bounded_membership_reconciliation':all(r['result']=='MATCH' for r in report['membership']),
                 'source_revisions_retained':max(Counter(r['logical_event_key'] for r in SnapshotReader(source,source_refs['snapshot']['snapshot_id']).commits['financial_events'].rows).values())>1}}
    validate_pr6_evidence(evidence,data_root=source,offline_root=recovery)
    report_dir.mkdir(parents=True,exist_ok=True)
    (report_dir/'run_manifest.json').write_text(json.dumps(evidence,indent=2)+'\n')
    (report_dir/'build_plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    print(json.dumps({'artifact_refs':source_refs,'gates':evidence['gates']},indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--plan',type=Path);parser.add_argument('--recovery-root',type=Path)
    parser.add_argument('--report-dir',type=Path);args=parser.parse_args()
    if args.plan:
        plan=json.loads(args.plan.read_text())
    else:
        plan=prepare_inputs(args.root)
        (RUN_ROOT/'build_plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    if args.recovery_root:
        if args.report_dir is None:parser.error('--report-dir required for validation')
        reference=RUN_ROOT/'data-root/evidence'
        if not (args.root/'evidence').exists():shutil.copytree(reference,args.root/'evidence')
        validate_run(args.root,args.recovery_root,plan,args.report_dir)
    else:
        result=build(args.root,plan)
        (RUN_ROOT/'build_result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
