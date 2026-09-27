"""REPRODUCTION_EVIDENCE: read-only real initialization and exact View comparison.

Usage: PYTHONPATH=src python3 reports/view-initialization/measure.py OUTPUT_DIRECTORY
The existing performance harness supplies the same IO counters and write guard.
"""
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import time
from collections import defaultdict
from contextlib import ExitStack
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('previous_measurement',
    Path(__file__).parents[1] / 'view-execution-performance/profile_real.py')
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
from axiom_data import artifacts, pr6_views
from axiom_data.view_validation import ViewValidationSession


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'result.json').exists():
        raise RuntimeError('validate existing result before rerunning')
    sys.addaudithook(previous.audit)
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('network forbidden'))
    baseline = json.loads((Path(__file__).parents[1] /
        'view-execution-performance/real-after.json').read_bytes())
    config = baseline['config']
    result = dict(status='RUNNING', pid=os.getpid(), snapshot=previous.SNAPSHOT,
                  config=config, phases={}, baseline_payload_digest=baseline['payload_digest'])
    calls = defaultdict(lambda: dict(calls=0, seconds=0.0))
    def timed(name, function):
        def invoke(*args, **kwargs):
            start = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                calls[name]['calls'] += 1
                calls[name]['seconds'] += time.perf_counter() - start
        return invoke
    def phase(name, function):
        previous.save(out / 'progress.json', dict(stage=name, pid=os.getpid()))
        print(json.dumps(dict(stage=name, pid=os.getpid())), flush=True)
        calls.clear()
        value, measurement = previous.measured(function)
        measurement['inclusive_function_profile'] = dict(calls)
        result['phases'][name] = measurement
        previous.save(out / 'partial.json', result)
        return value
    start = time.perf_counter()
    with ExitStack() as stack:
        for name in ('load_raw_batch', '_validate_domain_rows', '_validate_domain_commit_node',
                     '_validate_pr6_dependencies'):
            stack.enter_context(patch.object(artifacts, name, timed(name, getattr(artifacts, name))))
        session = ViewValidationSession(previous.ROOT, previous.SNAPSHOT)
        context = session.inputs('pr6_fact', config)
        reader = phase('initialization', context.__enter__)
        try:
            scope = {k:v for k,v in config.items() if k not in {'pit_policy','knowledge_cutoff'}}
            payload = phase('logical_comparison', lambda: pr6_views.project(
                reader, scope, config['pit_policy'], config['knowledge_cutoff']))
            digest = artifacts._digest(artifacts._json_bytes(payload))
            assert digest == baseline['payload_digest'], 'values/PIT/missing/provenance changed'
            result['payload_digest'] = digest
            result['exact_logical_equal'] = True
            def reuse():
                with session.inputs('pr6_fact', config) as reused:
                    assert reused is reader
            phase('reused_initialization', reuse)
            assert result['phases']['reused_initialization'].get('domain_loads', 0) == 0
        finally:
            phase('context_exit', lambda: context.__exit__(None, None, None))
    result.update(status='PASS', elapsed=time.perf_counter()-start)
    previous.save(out/'result.json', result)
    print(json.dumps(dict(status='PASS', elapsed=result['elapsed'])), flush=True)


if __name__ == '__main__':
    main()
