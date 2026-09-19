"""Bounded fixture evidence and read-only readiness for the frozen V1 target."""
import argparse
import json
from pathlib import Path
import shutil

from axiom_data import BuildApplication, SnapshotReader, load_raw_batch, validate_domain_commit_closure
from axiom_data.artifacts import _commit_ref, _digest, _json_bytes
from axiom_data.gate_a import make_gate_a_plan, validate_gate_a, validate_gate_a_report, completeness_matrix
from axiom_data.historical_sparse import plan_historical_sparse, execute_historical_sparse
from axiom_data.pr7_source import Pr7Builder


def fixture_probe(root, source_root):
    root = Path(root)
    if root.exists():
        raise ValueError('fixture evidence root already exists; validate retained output before reuse')
    source = json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
    source_root = Path(source_root)
    if not source_root.is_absolute() or source_root.is_symlink():
        raise ValueError('explicit absolute fixture source root required')
    # Frozen report records historical location; IDs remain its authority.
    SnapshotReader(source_root, source['refs']['snapshot_id'])
    shutil.copytree(source_root, root)
    reader = SnapshotReader(root, source['refs']['snapshot_id'])
    class EmptySource:
        calls = 0
        def query(self, endpoint, **kwargs):
            if endpoint != 'forecast':
                raise AssertionError('bounded probe requested another endpoint')
            self.calls += 1
            return []
    client = EmptySource()
    plan = plan_historical_sparse(symbols=['688981.SH'], start_session='2014-01-01', end_session='2026-09-08')
    kwargs = dict(run_id='gate-a-sparse-empty', plan=plan, domains=['forecast_observations'], client=client)
    first = execute_historical_sparse(root, **kwargs)
    second = execute_historical_sparse(root, **kwargs)
    if first != second or not first['complete'] or client.calls != 1:
        raise AssertionError('sparse empty resume changed results or repeated collection')
    observation = first['coverage'][0]['observations'][0]
    raw_id = observation['raw_ref']['raw_batch_id']
    parent = reader.commits['forecast_observations']
    builder = Pr7Builder(root, 'forecast_observations', builder_config={
        **parent.manifest['builder_config'], 'coverage_state_policy': 'source_observations.v2',
        'forecast_source_types': 'forecast_source_types.v1'},
        dependency_commit_ids={'security_master': reader.commits['security_master'].ref.commit_id})
    ref = BuildApplication('forecast_observations', builder).build(None, [raw_id], [], 'forecast_observations.v2')
    commit = validate_domain_commit_closure(root, 'forecast_observations', ref.commit_id)
    coverage = commit.manifest['source_coverage']
    if (coverage['schema_version'] != 'source_observations.v2' or len(commit.rows) != 0
            or len(coverage['observations']) != 1 or not coverage['observations'][0]['payload_admission']['complete']):
        raise AssertionError('complete empty coverage did not enter DomainCommit identity')
    return {'evidence_kind': 'deterministic_fixture_not_live_supplier', 'fixture_root': str(root),
            'source_calls': client.calls, 'resume_idempotent': first == second,
            'sparse_coverage': first, 'domain_commit_ref': _commit_ref(commit),
            'domain_rows': len(commit.rows), 'source_coverage': coverage,
            'raw_payload_unchanged_empty': load_raw_batch(root, raw_id).payload == b'[]'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fixture-root', type=Path)
    parser.add_argument('--fixture-source-root', type=Path)
    args = parser.parse_args()
    if (args.fixture_root is None) != (args.fixture_source_root is None):
        parser.error('--fixture-root and --fixture-source-root must be supplied together')
    if args.output.exists():
        raise ValueError('explicit evidence output already exists')
    args.output.mkdir(parents=True)
    target = json.loads(Path('reports/pr8/industry_authority/qualification.json').read_bytes())['scope']
    scope = dict(symbols=target['symbols'], start_session='2014-01-01', end_session='2026-09-08',
                 financial_observation_start='2013-01-01', benchmarks=['000300.SH'], universe_ids=['000906.SH'])
    plan = make_gate_a_plan(scope)
    report = validate_gate_a(plan)
    # Exact immutable code/profile/plan binding is independently replayable via
    # validate_gate_a_report; do not run the full planner twice in this producer.
    outputs = {'plan.json': plan, 'gate_a.json': report, 'completeness_matrix.json': completeness_matrix()}
    if args.fixture_root is not None:
        outputs['sparse_empty.json'] = fixture_probe(args.fixture_root, args.fixture_source_root)
    for name, value in outputs.items():
        (args.output / name).write_bytes(_json_bytes(value))
    print(json.dumps({'status': report['status'], 'code_revision': report['evidence']['code_identity']['code_revision'],
                      'output': str(args.output), 'target_securities': len(scope['symbols']),
                      'report_digest': report['report_digest'], 'findings': report['findings']}))
    return 0 if report['status'] == 'GATE_A_READY_FOR_BULK_BUILD' else 1


if __name__ == '__main__':
    raise SystemExit(main())
