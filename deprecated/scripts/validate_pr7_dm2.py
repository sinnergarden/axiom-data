"""Build, consume and compare D-M2 artifacts under network/legacy read denial."""
import argparse
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from axiom_data import SnapshotReader,rebuild_catalog
from axiom_data.artifacts import _layout
from axiom_data.dm2_admission import consumer_admission
from axiom_data.offline_guard import deny_external_data
from axiom_data.pr7_reconciliation import reconcile
from run_pr7_dm2 import prepare,build


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def freeze_reference(package,target):
    mapping=json.loads((package/'shareholder_provenance_map.json').read_text())
    entries=[]
    for item in mapping['formal_s180_chain']['files']:
        name=Path(item['frozen_path']).name;content=(package/item['frozen_path']).read_bytes()
        if hashlib.sha256(content).hexdigest()!=item['sha256']:raise ValueError('historical evidence digest mismatch')
        (target/name).parent.mkdir(parents=True,exist_ok=True);(target/name).write_bytes(content)
        entries.append({'path':name,'sha256':item['sha256'],'frozen_path':item['frozen_path']})
    write(target/'reference_manifest.json',{'source_artifact':mapping['formal_s180_chain']['artifact_id'],'files':entries})


def validated_build(target,plan):
    with deny_external_data(target) as attempts:
        # Prove the guard, before the actual consumer. The denied paths need not exist.
        for path in ('/tmp/SysQ/data/market.parquet','/tmp/legacy/qlib/features/close.day.bin','/tmp/legacy/sidecars/model.json'):
            try:Path(path).read_bytes()
            except PermissionError:pass
            else:raise AssertionError('legacy guard inactive')
        import socket
        try:socket.create_connection(('example.invalid',443))
        except PermissionError:pass
        else:raise AssertionError('network guard inactive')
        probes=list(attempts);attempts.clear()
        refs=build(target,plan)
        admission=consumer_admission(target,refs)
        count=rebuild_catalog(target)
        _layout(target).catalog.unlink()
        if rebuild_catalog(target)!=count:raise AssertionError('catalog rebuild mismatch')
        if attempts:raise AssertionError('consumer attempted a denied dependency')
        reader=SnapshotReader(target,refs['snapshot_id'])
        qualification=[]
        for domain,commit in sorted(reader.commits.items()):
            counts=Counter(r.get('pit_qualification','best_effort') for r in commit.rows)
            qualification.append({'domain':domain,'rows':len(commit.rows),'counts':{k:counts[k] for k in ('verified','observed','best_effort','unknown','unsupported')},
                'historical_bootstrap_ratio':counts['best_effort']/len(commit.rows) if commit.rows else None,
                'symbols':sorted({r['symbol'] for r in commit.rows if 'symbol' in r}),
                'classification_basis':'row qualification; baseline market/calendar/security identity uses terminal bootstrap source evidence when row schema has no qualification'})
        return {'refs':refs,'admission':admission,'catalog_entries':count,'pit_qualification':qualification,
                'legacy_kill':{'status':'PASS','denied_probes':probes,'unexpected_attempts':attempts,'requirements':56}}


def main(args):
    run=args.run_root;admission_root=run/args.admission_name;source=admission_root/'source';recovery=admission_root/'recovery'
    if args.recover:
        plan=json.loads(args.plan.read_text());result=validated_build(args.target,plan);write(args.target/'validated_result.json',result);print('recovery validated',result['admission']['requirement_count']);return
    if source.exists() or recovery.exists():raise ValueError('explicit admission identity already exists; independently validate it or use a new run')
    plan=prepare(source,run);write(admission_root/'build_plan.json',plan)
    freeze_reference(args.package,source/'evidence'/'shareholder')
    result=validated_build(source,plan);write(source/'validated_result.json',result)
    source_reconcile=reconcile(source,result['refs'],source/'evidence'/'shareholder')
    # Frozen implementation is actually executed in a separate interpreter below.
    repo=Path(__file__).resolve().parents[1];bundle=admission_root/'code_bundle'
    for directory in ('src','scripts'):
        for p in (repo/directory).rglob('*'):
            if p.is_file() and p.suffix in {'.py','.json'}:
                dest=bundle/p.relative_to(repo);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(p.read_bytes())
    code_manifest={p.relative_to(bundle).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(bundle.rglob('*')) if p.is_file()}
    write(admission_root/'code_manifest.json',code_manifest)
    shutil.copytree(source/'raw',recovery/'raw')
    import subprocess,sys,os
    env=dict(os.environ,PYTHONPATH=str(bundle/'src'),PYTHONDONTWRITEBYTECODE='1')
    subprocess.run([sys.executable,str(bundle/'scripts'/'validate_pr7_dm2.py'),'--recover','--run-root',str(run),'--target',str(recovery),'--plan',str(admission_root/'build_plan.json')],check=True,env=env,cwd=bundle)
    recovered=json.loads((recovery/'validated_result.json').read_text())
    if result!=recovered:raise AssertionError('source/recovered identities or logical results differ')
    reports=repo/'reports'/'pr7';write(reports/'coverage_and_feature_admission.json',result['admission'])
    write(reports/'pit_qualification.json',result['pit_qualification']);write(reports/'reconciliation.json',source_reconcile)
    write(reports/'legacy_kill.json',result['legacy_kill'])
    write(reports/'offline_rebuild.json',{'status':'PASS','source_root':str(source),'recovery_root':str(recovery),
        'source_refs':result['refs'],'recovery_refs':recovered['refs'],'catalog_entries':result['catalog_entries'],
        'logical_digest':result['admission']['logical_digest'],'code_manifest':code_manifest,'code_bundle_root':str(bundle),
        'checks':['domain identities','contracts','logical rows','PIT selection','historical union','lookback','derived refs','public fields','Qlib binary','56 requirements','469 Feature dependencies','catalog deletion/rebuild']})
    write(reports/'run_manifest.json',{'schema_version':'pr7_dm2_run.v1','source_root':str(source),'recovery_root':str(recovery),'refs':result['refs'],'status':'PASS','stage':'bounded D-M2 artifact admission; review pending'})
    print('D-M2 bounded closure: 56 requirements, 469 dependencies, offline match')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-root',type=Path,required=True);p.add_argument('--package',type=Path);p.add_argument('--admission-name',default='admission');p.add_argument('--recover',action='store_true');p.add_argument('--target',type=Path);p.add_argument('--plan',type=Path)
    main(p.parse_args())
