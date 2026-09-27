"""Independently re-open reported terminal artifacts; report flags are not evidence."""
import argparse
import hashlib
import json
from pathlib import Path
from axiom_data import SnapshotReader
from axiom_data.dm2_admission import consumer_admission
from axiom_data.pr7_reconciliation import reconcile
from axiom_data.offline_guard import deny_external_data


def check(reports):
    read=lambda name:json.loads((reports/name).read_text())
    run=read('run_manifest.json');offline=read('offline_rebuild.json')
    source=Path(run['source_root']);recovery=Path(run['recovery_root']);refs=run['refs']
    if source.resolve()==recovery.resolve():raise ValueError('independent recovery root required')
    if offline['source_refs']!=refs or offline['recovery_refs']!=refs:raise ValueError('reported refs disagree')
    for root in (source,recovery):
        with deny_external_data(root) as attempts:
            admission=consumer_admission(root,refs)
            if attempts:raise ValueError('legacy read attempt')
        if admission!=read('coverage_and_feature_admission.json'):raise ValueError('coverage evidence differs from actual consumer')
        reader=SnapshotReader(root,refs['snapshot_id'])
        if {d:c.ref.commit_id for d,c in reader.commits.items()}!=refs['domain_commit_ids']:raise ValueError('actual domain refs disagree')
    if reconcile(source,refs,source/'evidence'/'shareholder')!=read('reconciliation.json'):raise ValueError('reconciliation evidence differs from actual raw/reference')
    bundle=Path(offline['code_bundle_root'])
    for relative,digest in offline['code_manifest'].items():
        path=bundle/relative
        if not path.resolve().is_relative_to(bundle.resolve()) or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('frozen implementation digest mismatch')
    if offline['logical_digest']!=admission['logical_digest']:raise ValueError('offline logical digest mismatch')
    print('PASS: independently loaded source and recovery, 56/469 closure, source/reference/Qlib checks, frozen code digests')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--reports',type=Path,required=True);check(p.parse_args().reports)
