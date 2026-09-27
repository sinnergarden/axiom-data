"""REPRODUCTION_EVIDENCE: bounded read-only validation of a real small Snapshot."""
import json
from pathlib import Path
import time
from unittest.mock import patch

from axiom_data import SnapshotReader, artifacts
from axiom_data.event_views import project
from axiom_data.view_validation import ViewValidationSession
from fixture_locations import fixture_root


def main():
    run=json.loads(Path('deprecated/history/reports/pr7/run_manifest.json').read_bytes())
    root=fixture_root(run['source_root']);snapshot=run['refs']['snapshot_id']
    config=dict(symbols=['688981.SH'],start_session='2025-06-10',end_session='2025-06-13',
        pit_policy='best_effort_vendor_v1',knowledge_cutoff='2025-06-13T23:59:59+08:00')
    scope={k:config[k] for k in ('symbols','start_session','end_session')}
    session=ViewValidationSession(root,snapshot)
    results={};outputs=[]
    for mode in ('full','scoped','reused'):
        with patch.object(artifacts,'load_domain_commit',wraps=artifacts.load_domain_commit) as calls:
            started=time.perf_counter()
            if mode=='full':
                reader=SnapshotReader(root,snapshot)
                elapsed=time.perf_counter()-started
                outputs.append(project(reader,scope,config['pit_policy'],config['knowledge_cutoff']))
            else:
                with session.inputs('pr7_fact',config) as reader:
                    elapsed=time.perf_counter()-started
                    outputs.append(project(reader,scope,config['pit_policy'],config['knowledge_cutoff']))
            results[mode]=dict(initialization_seconds=elapsed,domain_loads=calls.call_count,
                domains=sorted({c.args[1] for c in calls.call_args_list}))
    assert outputs[0]==outputs[1]==outputs[2]
    print(json.dumps(dict(snapshot=snapshot,config=config,logical_equal=True,results=results,
        limitation='small real forensic Snapshot, warm OS caches; no production-scale latency claim'),indent=2))


if __name__=='__main__':main()
