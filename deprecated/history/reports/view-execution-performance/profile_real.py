"""REPRODUCTION_EVIDENCE: immutable input, isolated output, no supplier access."""
import argparse
import cProfile
import json
import os
from pathlib import Path
import pstats
import socket
import sys
import time
from collections import defaultdict, Counter
from contextlib import ExitStack
from unittest.mock import patch

from axiom_data import artifacts
from axiom_data import pr6_coverage, pr6_views
from axiom_data.view_validation import ViewValidationSession
from axiom_data.partition_rows import PartitionRows

HERE = Path(__file__).resolve().parent
ROOT = Path('/var/lib/axiom-data')
SNAPSHOT = 'snapshot-1d69dc236a358f1627ae91080c33cf993b9e2acb55bbd2c2cb7a37129ce6b5a5'


def save(path, value):
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(value,indent=2)+'\n')
    pending.replace(path)


def audit(event, args):
    if event == 'open':
        path, mode, flags = args
        if isinstance(path,(str,bytes,os.PathLike)) and Path(os.fsdecode(path)).absolute().is_relative_to(ROOT):
            if (isinstance(mode,str) and any(x in mode for x in 'wax+')) or flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC):
                raise RuntimeError('formal root write denied')
    if event in {'os.remove','os.rmdir','os.mkdir','os.rename','os.chmod','os.chown','os.link','os.symlink','os.truncate'}:
        for arg in args[:2]:
            if isinstance(arg,(str,bytes,os.PathLike)) and Path(os.fsdecode(arg)).absolute().is_relative_to(ROOT):
                raise RuntimeError('formal root mutation denied')


def io():
    return {k:int(v) for line in Path('/proc/self/io').read_text().splitlines()
            for k,v in [line.split(':',1)] if k in {'rchar','read_bytes','wchar','write_bytes'}}


def measured(fn):
    counts=Counter(); domains=Counter(); original_rows=PartitionRows._rows; original_load=artifacts.load_domain_commit
    def rows(obj, entry):
        counts['partition_traversals']+=1; counts['partition_bytes']+=entry['bytes']
        domains[obj.domain]+=1
        yield from original_rows(obj,entry)
    def load(*a,**k):
        counts['domain_loads']+=1
        return original_load(*a,**k)
    before=io(); started=time.perf_counter()
    with patch.object(PartitionRows,'_rows',rows), patch.object(artifacts,'load_domain_commit',load):
        value=fn()
    after=io()
    return value,dict(seconds=time.perf_counter()-started,**counts,partitions_by_domain=dict(domains),
                      **{k:after[k]-before[k] for k in before})


def run(mode):
    out = HERE / mode
    out.mkdir(exist_ok=True)
    if (out / 'result.json').exists():
        raise RuntimeError('existing result requires verification, not another run')
    sys.addaudithook(audit)
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('network forbidden'))
    plan = json.loads((ROOT / 'operations/v1-full-bootstrap-20260913-r1/required-views.json').read_bytes())
    config = dict(next(v['config'] for v in plan.values()
                       if v['kind'] == 'pr6_fact' and v['config']['symbols'] == ['688981.SH']))
    config.update(start_session='2026-08-24', end_session='2026-09-11')
    scope = {k:v for k,v in config.items() if k not in {'pit_policy','knowledge_cutoff'}}
    result = dict(status='RUNNING', snapshot=SNAPSHOT, config=config, pid=os.getpid(), phases={})
    calls = defaultdict(lambda: dict(calls=0, seconds=0.0))
    def wrap(name, fn):
        def timed(*args, **kwargs):
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                calls[name]['calls'] += 1
                calls[name]['seconds'] += time.perf_counter() - start
        return timed
    def phase(label, fn):
        print(json.dumps(dict(stage=label,pid=os.getpid())),flush=True)
        save(out/'progress.json',dict(stage=label,pid=os.getpid()))
        calls.clear()
        value, metrics = measured(fn)
        metrics['inclusive_function_profile'] = dict(calls)
        result['phases'][label] = metrics
        save(out/'partial.json',result)
        return value
    started = time.perf_counter()
    with ExitStack() as patches:
        for module, names in [(artifacts, ['load_raw_batch','_validate_domain_rows',
                '_validate_dm1_observation_refs','_validate_pr6_dependencies','_validate_market_dependencies',
                '_validate_domain_commit_node','validate_dm1_snapshot_rows']),
                (pr6_coverage,['admit_view','membership_coverage','select_financial_revisions','financial_ambiguities'])]:
            for name in names:
                patches.enter_context(patch.object(module,name,wrap(name,getattr(module,name))))
        session = ViewValidationSession(ROOT,SNAPSHOT)
        context = session.inputs('pr6_fact',config)
        reader = phase('initialization',context.__enter__)
        try:
            profile = cProfile.Profile()
            def project():
                return pr6_views.project(reader,scope,config['pit_policy'],config['knowledge_cutoff'])
            def profiled():
                profile.enable()
                try:return project()
                finally:profile.disable()
            payload = phase('projection',profiled)
            profile.dump_stats(out/'projection.prof')
            with (out/'projection-profile.txt').open('w') as stream:
                pstats.Stats(profile,stream=stream).sort_stats('cumulative').print_stats(65)
            content = artifacts._json_bytes(payload)
            (out/'payload.json').write_bytes(content)
            result['payload_digest'] = artifacts._digest(content)
            if mode != 'before':
                assert content == (HERE/'before/payload.json').read_bytes(), 'logical output changed'
                warm = phase('warm_projection',project)
                assert artifacts._json_bytes(warm) == content
                result['exact_equal'] = True
                changed = dict(config,start_session='2026-09-01')
                def changed_scope():
                    with session.inputs('pr6_fact',changed) as reused:
                        assert reused is reader
                phase('changed_scope_validation',changed_scope)
                assert result['phases']['changed_scope_validation'].get('domain_loads',0) == 0
                # Use the public operation in an isolated output root, with the
                # same dependency-validated Reader. Canonical rows keep formal
                # input paths. This only avoids another already-measured init.
                from axiom_data import consumption, view_operation
                output = out / 'outputs'
                output.mkdir(exist_ok=True)
                reader.data_root = output
                views = {'financial':dict(kind='pr6_fact',config=config)}
                def operation():
                    return view_operation.materialize_views(output,run_id='financial-performance',
                        snapshot_id=SNAPSHOT,views=views)
                with patch.object(consumption,'SnapshotReader',lambda *a,**k:reader):
                    with patch.object(pr6_views,'_build_pr6_fact_view',wraps=pr6_views._build_pr6_fact_view) as builder:
                        built = phase('prepared_public_build',operation)
                        assert built['status'] == 'VIEWS_BUILT',built
                        result['phases']['prepared_public_build']['builder_calls'] = builder.call_count
                    with patch.object(pr6_views,'_build_pr6_fact_view',side_effect=AssertionError('resume entered builder')) as builder:
                        resumed = phase('completed_resume',operation)
                        assert resumed['status'] == 'VIEWS_BUILT',resumed
                        result['phases']['completed_resume']['builder_calls'] = builder.call_count
                        assert builder.call_count == 0
                view_id = built['published_views']['financial']['view_id']
                artifact = output/'derived/pr6_fact/commits'/view_id
                assert (artifact/'rows.json').read_bytes() == content
                result['view_id'] = view_id
                result['artifact_payload_equal'] = True
        finally:
            phase('context_exit',lambda:context.__exit__(None,None,None))
    result.update(status='PASS',total_seconds=time.perf_counter()-started)
    save(out/'result.json',result)
    print(json.dumps(dict(status='PASS',mode=mode,total_seconds=result['total_seconds'])),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode');args=parser.parse_args()
    run(args.mode)
