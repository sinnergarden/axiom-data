"""REPRODUCTION_EVIDENCE: real batch projection and independent publication.

Usage: PYTHONPATH=src python3 reports/financial-operation-batch/profile_real.py OUT BASELINE
BASELINE is the archived implementation's complete 50-security result.json.
Formal inputs are read-only; published test Views live beneath OUT.
"""
import importlib.util
import json
import os
import resource
from pathlib import Path
import socket
import sys
import time
from collections import defaultdict
from contextlib import ExitStack
from unittest.mock import patch

from axiom_data import artifacts, consumption, pr6_coverage, pr6_views, view_operation
from axiom_data.view_validation import ViewValidationSession

spec = importlib.util.spec_from_file_location('measurement',
    Path(__file__).resolve().parents[1] / 'view-execution-performance/profile_real.py')
measurement = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measurement)


def run(out, baseline_path):
    out.mkdir(exist_ok=True)
    if (out / 'result.json').exists():
        raise RuntimeError('validate existing terminal result before another run')
    baseline = json.loads(baseline_path.read_bytes())
    assert baseline['status'] == 'PASS' and len(baseline['views']) == 50
    assert baseline['snapshot'] == measurement.SNAPSHOT
    configs = baseline['configs']
    sys.addaudithook(measurement.audit)
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('network forbidden'))
    result = dict(status='RUNNING', snapshot=measurement.SNAPSHOT, pid=os.getpid(),
                  configs=configs, phases={}, views=[], milestones={})
    calls = defaultdict(lambda: dict(calls=0, seconds=0.0))
    def timed(name, fn):
        def invoke(*args, **kwargs):
            started = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                calls[name]['calls'] += 1
                calls[name]['seconds'] += time.perf_counter() - started
        return invoke
    def phase(name, fn):
        measurement.save(out / 'progress.json', dict(stage=name, pid=os.getpid()))
        print(json.dumps(dict(stage=name, pid=os.getpid())), flush=True)
        calls.clear()
        value, metrics = measurement.measured(fn)
        metrics['inclusive_function_profile'] = dict(calls)
        result['phases'][name] = metrics
        measurement.save(out / 'partial.json', result)
        return value
    def project(reader, config):
        scope = {k:v for k,v in config.items() if k not in {'pit_policy', 'knowledge_cutoff'}}
        return pr6_views.project(reader, scope, config['pit_policy'], config['knowledge_cutoff'])
    started = time.perf_counter()
    with ExitStack() as stack:
        for module, names in [
                (pr6_coverage, ['admit_view', '_admission_inputs', 'select_financial_revisions',
                                'financial_ambiguities', 'membership_coverage']),
                (pr6_views, ['_valuation_at', 'select_financial_revisions', 'financial_derived']),
                (consumption.SnapshotReader, ['facts', 'trading_calendar', 'members', 'industry_facts'])]:
            for name in names:
                stack.enter_context(patch.object(module, name, timed(module.__name__ + '.' + name, getattr(module, name))))
        session = ViewValidationSession(measurement.ROOT, measurement.SNAPSHOT)
        context = session.inputs('pr6_fact', configs[0])
        reader = phase('initialization', context.__enter__)
        try:
            with pr6_coverage.financial_batch(reader):
                for index, (config, expected) in enumerate(zip(configs, baseline['views']), 1):
                    payload = phase('security_' + str(index), lambda:project(reader, config))
                    content = artifacts._json_bytes(payload)
                    digest = artifacts._digest(content)
                    assert config['symbols'] == [expected['symbol']]
                    assert digest == expected['digest'], config['symbols']
                    result['views'].append(dict(symbol=expected['symbol'], digest=digest,
                                               bytes=len(content), wide_rows=len(payload['wide'])))
                    del payload, content
                    if index in {1, 10, 50}:
                        phases = [v for k,v in result['phases'].items() if k.startswith('security_')]
                        result['milestones'][str(index)] = {key:sum(p.get(key, 0) for p in phases)
                            for key in ('seconds', 'domain_loads', 'partition_traversals', 'partition_bytes', 'rchar', 'read_bytes')}
                    measurement.save(out / 'partial.json', result)
                result['equivalent_rejections'] = []
                for rejection in baseline['rejections']:
                    rejected = rejection['config']
                    try:
                        phase('existing_gap_' + rejected['symbols'][0], lambda:project(reader, rejected))
                    except artifacts.ArtifactError as exc:
                        assert str(exc) == rejection['error']
                        result['equivalent_rejections'].append(rejection)
                    else:
                        raise AssertionError('existing valuation gap was hidden')
                histories = reader._financial_batch['histories']
                result['shared_history'] = dict(securities=len(histories),
                    encoded_bytes=sum(map(len, histories.values())),
                    peak_process_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            # Use the same fully dependency-validated Reader, with only the
            # output destination redirected. Canonical paths remain formal/read-only.
            output = out / 'outputs'
            output.mkdir(exist_ok=True)
            reader.data_root = output
            serial = phase('serial_publication', lambda:pr6_views._build_pr6_fact_view(reader, **configs[0]))
            views = {str(i):dict(kind='pr6_fact', config=config) for i,config in enumerate(configs)}
            def operation():
                return view_operation.materialize_views(output, run_id='financial-batch-performance',
                    snapshot_id=measurement.SNAPSHOT, views=views)
            with patch.object(consumption, 'SnapshotReader', lambda *a, **k:reader):
                with patch.object(pr6_views, '_build_pr6_fact_view', wraps=pr6_views._build_pr6_fact_view) as builder:
                    built = phase('public_batch_build', operation)
                    assert built['status'] == 'VIEWS_BUILT', built
                    result['phases']['public_batch_build']['builder_calls'] = builder.call_count
                    assert builder.call_count == 50
                assert built['published_views']['0']['view_id'] == serial.view_id
                assert built['published_views']['0']['manifest_digest'] == serial.manifest_digest
                with patch.object(pr6_views, '_build_pr6_fact_view', side_effect=AssertionError('resume entered builder')) as builder:
                    resumed = phase('completed_resume', operation)
                    assert resumed['status'] == 'VIEWS_BUILT', resumed
                    assert builder.call_count == 0
                    result['phases']['completed_resume']['builder_calls'] = 0
            for index, expected in enumerate(baseline['views']):
                ref = built['published_views'][str(index)]
                content = (output / 'derived/pr6_fact/commits' / ref['view_id'] / 'rows.json').read_bytes()
                assert artifacts._digest(content) == expected['digest']
            result.update(published_views=built['published_views'], logical_equal_count=50,
                          serial_batch_identity_equal=True, resume_builder_calls=0)
        finally:
            phase('context_exit', lambda:context.__exit__(None, None, None))
    result.update(status='PASS', elapsed=time.perf_counter() - started)
    measurement.save(out / 'result.json', result)
    print(json.dumps(dict(status='PASS', elapsed=result['elapsed'])), flush=True)


if __name__ == '__main__':
    run(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
