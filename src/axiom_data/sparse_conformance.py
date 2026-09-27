"""Offline behavioral evidence for the public sparse observation executor.

Temporary Raw and collection checkpoints exercise current admission, including
resume. This proves request-complete best-effort behavior, never historical PIT
or source availability. A separate malformed-gap fixture additionally exercises
the shared aggregate body with a resolver fault, so redundant provenance
validation cannot conceal a missing interval guard.
"""
import copy
import json
import shutil
import tempfile
from pathlib import Path

from axiom_data import historical_sparse
from axiom_data.artifacts import ArtifactError, _digest, _json_bytes, load_raw_batch, write_raw_batch
from axiom_data.operations import _request, _source_binding, collect_requests
from axiom_data.source_completeness import completeness_policy, validate_raw_completeness


PUBLIC_PATH = [
    'axiom_data.historical_sparse.plan_historical_sparse',
    'axiom_data.historical_sparse.execute_historical_sparse',
    'axiom_data.historical_sparse.validate_sparse_coverage',
    'axiom_data.source_completeness.validate_raw_completeness',
]
RUN_ID = 'sparse-conformance-v1'
DOMAINS = ['forecast_observations']
SCOPE = dict(symbols=['600036.SH'], start_session='2014-01-01', end_session='2014-01-04')
CASES = {
    'exact_complete_children': 'COMPLETE',
    'missing_child': 'PARTIAL',
    'interval_gap': 'REJECTED:sparse split differs from retained parent Raw',
    'truncated_child': 'REJECTED:possibly truncated source payload; split bounded request',
    'partial_child': 'REJECTED:partial or truncated Raw cannot be admitted',
    'selector_mismatch': 'REJECTED:sparse split differs from retained parent Raw',
    'conflicting_overlap': 'REJECTED:sparse split differs from retained parent Raw',
    'empty_child_wrong_scope': 'REJECTED:resume RawBatch/source request binding mismatch',
}


def _read(path):
    return json.loads(path.read_bytes())


def _save(path, value):
    path.write_bytes(_json_bytes(value))


def _checkpoint(root, batch, spec):
    return root / 'operations' / batch / 'collection-checkpoints' / (_request(spec)[7:] + '.json')


def _row(day='20140101', kind='预增'):
    return dict(ts_code='600036.SH', ann_date=day, end_date='20140331', type=kind)


class _FixtureClient:
    """An explicit in-memory client: no source client or network fallback."""
    def __init__(self, cap, *, overlap=False):
        self.cap = cap
        self.overlap = overlap
        self.calls = 0

    def query(self, endpoint, **params):
        self.calls += 1
        if endpoint != 'forecast':
            raise AssertionError('fixture queried an unexpected endpoint')
        if params['start_date'] == '20140101' and params['end_date'] == '20140104':
            return [_row()] * self.cap
        if self.overlap:
            return [_row('20140102', '预增' if params['start_date'] == '20140101' else '预减')]
        return []


def _shape(result):
    """Malformed output is a failed check, never a successful rejection."""
    if (not isinstance(result, dict) or result.get('status') not in {'COMPLETE', 'PARTIAL'}
            or result.get('complete') is not (result['status'] == 'COMPLETE')
            or not isinstance(result.get('coverage'), list) or len(result['coverage']) != 1):
        raise ValueError('sparse validator returned malformed aggregate evidence')
    return result['status']


def _seed(root, plan, cap):
    client = _FixtureClient(cap)
    result = historical_sparse.execute_historical_sparse(root, run_id=RUN_ID,
        plan=plan, domains=DOMAINS, client=client)
    if _shape(result) != 'COMPLETE':
        raise ValueError('valid exact partition did not complete')
    parent = result['coverage'][0]
    children = parent['children']
    if (len(children) != 2 or len(parent['observations']) != 2 or parent.get('empty') is not True
            or any(child.get('complete') is not True or child.get('empty') is not True for child in children)):
        raise ValueError('exact partition lost child completeness or empty observations')
    admissions = [validate_raw_completeness(load_raw_batch(root, child['raw_batch_id'])) for child in children]
    if any(admission.get('complete') is not True or admission.get('row_count') != 0
           or admission.get('scope') != child['request']['params']
           or admission.get('historical_completeness') != 'request_complete_best_effort'
           or admission.get('empty_result_semantics') != 'no_source_events_in_scope'
           for admission, child in zip(admissions, children)):
        raise ValueError('empty child lost formal scope or best-effort admission')
    retained = load_raw_batch(root, parent['raw_batch_id'])
    if len(json.loads(retained.payload)) != cap:
        raise ValueError('split parent does not retain the real fixed-cap response')
    resumed = historical_sparse.execute_historical_sparse(root, run_id=RUN_ID,
        plan=plan, domains=DOMAINS, client=client)
    if resumed != result or client.calls != 3:
        raise ValueError('checkpoint resume changed evidence or recollected source requests')
    return parent


def _replace_raw(root, checkpoint, spec, *, rows, summary=None):
    binding = _source_binding(spec)
    raw = write_raw_batch(root, 'conformance-mutated-child', domain=spec['domain'],
        source_profile=binding['source_profile_ref'], source_profile_version=binding['source_profile_version'],
        source_profile_digest=binding['source_profile_digest'],
        request=dict(endpoint=spec['endpoint'], params=spec['params'], fields=binding['fields']),
        retrieved_at='2014-01-05T00:00:00Z', payload=_json_bytes(rows),
        collector_code='sparse-conformance.v1', summary=summary)
    record = _read(checkpoint)
    record.update(raw_batch_id=raw.raw_batch_id, state='VALID_COMPLETE')
    _save(checkpoint, record)


def _malformed_split(root, parent, case, cap):
    """Collect independently valid children, then bind the faulty split graph."""
    parent_path = _checkpoint(root, parent['run_id'], parent['request'])
    record = _read(parent_path)
    split = copy.deepcopy(record['split'])
    left, right = split['requests']
    if case == 'interval_gap':
        right['params']['start_date'] = '20140104'
        right['economic_scope']['start'] = '20140104'
    elif case == 'selector_mismatch':
        right['params']['ts_code'] = '000001.SZ'
    elif case == 'conflicting_overlap':
        right['params']['start_date'] = '20140102'
        right['economic_scope']['start'] = '20140102'
    else:
        raise ValueError('unknown malformed split fixture')
    batch = RUN_ID + '-split-' + _digest(_json_bytes({'parent_batch': parent['run_id'], 'split': split}))[7:31]
    collected = collect_requests(root, run_id=batch, requests=split['requests'],
        client=_FixtureClient(cap, overlap=case == 'conflicting_overlap'))
    if collected['status'] != 'COMPLETE' or len(collected['completed']) != 2:
        raise ValueError('malformed split fixture failed to collect its independently valid children')
    for child in split['requests']:
        saved = _read(_checkpoint(root, batch, child))
        admission = validate_raw_completeness(load_raw_batch(root, saved['raw_batch_id']))
        if admission.get('complete') is not True or admission.get('scope') != child['params']:
            raise ValueError('malformed split child admission differs from its own scope')
    record.update(split=split, child_refs={'run_id': batch, 'request_ids': [_request(c) for c in split['requests']]})
    _save(parent_path, record)
    return split


def run_sparse_conformance():
    """Return stable per-fixture outcomes; any setup/error/mismatch blocks."""
    evidence = dict(schema_version='sparse_conformance.v1', status='BLOCKED',
        public_path=PUBLIC_PATH, qualification='request_complete_best_effort',
        empty_result_semantics='no_source_events_in_scope',
        evidence_kind='offline_behavior_not_historical_source_availability',
        ready_for_consumption=False, cases=[])
    with tempfile.TemporaryDirectory(prefix='axiom-sparse-conformance-') as temporary:
        directory = Path(temporary)
        baseline = directory / 'baseline'
        try:
            plan = historical_sparse.plan_historical_sparse(**SCOPE)
            cap = completeness_policy('tushare_events.v1', 'forecast')['limit']
            if type(cap) is not int or cap <= 0:
                raise ValueError('forecast fixture requires its real declared positive cap')
            parent = _seed(baseline, plan, cap)
        except Exception as exc:
            evidence['cases'] = [dict(fixture_id=name, expected=expected,
                actual='SETUP_ERROR', passed=False, error_type=type(exc).__name__, reason=str(exc))
                for name, expected in CASES.items()]
            return evidence
        evidence['fixture_scope'] = copy.deepcopy(SCOPE)
        evidence['source_limit'] = cap
        evidence['cases'].append(dict(fixture_id='exact_complete_children', expected='COMPLETE',
            actual='COMPLETE', passed=True, child_count=2, admitted_empty_observations=2,
            retained_parent_rows=cap, source_query_count=3, resume_query_count=0))
        for case, expected in list(CASES.items())[1:]:
            item = dict(fixture_id=case, expected=expected, actual='SETUP_ERROR', passed=False,
                        validator_path=PUBLIC_PATH[2])
            evidence['cases'].append(item)
            root = directory / case
            try:
                shutil.copytree(baseline, root)
                children = parent['children']
                child = children[0]
                checkpoint = _checkpoint(root, child['run_id'], child['request'])
                split = None
                if case == 'missing_child':
                    checkpoint.unlink()
                elif case in {'interval_gap', 'selector_mismatch', 'conflicting_overlap'}:
                    split = _malformed_split(root, parent, case, cap)
                elif case == 'truncated_child':
                    _replace_raw(root, checkpoint, child['request'], rows=[_row()] * cap)
                elif case == 'partial_child':
                    _replace_raw(root, checkpoint, child['request'], rows=[], summary={'partial': True})
                elif case == 'empty_child_wrong_scope':
                    record = _read(checkpoint)
                    other_raw = load_raw_batch(root, children[1]['raw_batch_id'])
                    if json.loads(other_raw.payload) != [] or other_raw.manifest['request']['params'] == child['request']['params']:
                        raise ValueError('wrong-scope fixture requires empty Raw with a different actual scope')
                    record.update(state='VALID_COMPLETE', raw_batch_id=other_raw.ref.raw_batch_id)
                    _save(checkpoint, record)
            except Exception as exc:
                item.update(error_type=type(exc).__name__, reason=str(exc))
                continue
            try:
                actual = historical_sparse.validate_sparse_coverage(root, run_id=RUN_ID, plan=plan, domains=DOMAINS)
                item['actual'] = _shape(actual)
                item['child_statuses'] = [c['status'] for c in actual['coverage'][0]['children']]
            except ArtifactError as exc:
                item.update(actual='REJECTED:' + str(exc), error_type=type(exc).__name__)
            except Exception as exc:
                item.update(actual='EXECUTION_ERROR', error_type=type(exc).__name__, reason=str(exc))
            item['passed'] = item['actual'] == expected
            if case == 'interval_gap':
                isolated = dict(fixture_id='gap_guard_isolation',
                    expected='REJECTED:sparse child interval gap or overlap', actual='EXECUTION_ERROR', passed=False,
                    validator_path='axiom_data.historical_sparse._validate_sparse_coverage',
                    boundary_injection='controlled_split_provenance_isolation_not_public_source_proof')
                evidence['cases'].append(isolated)
                try:
                    actual = historical_sparse._validate_sparse_coverage(root, run_id=RUN_ID,
                        plan=plan, domains=DOMAINS, split_resolver=lambda raw: copy.deepcopy(split))
                    isolated['actual'] = _shape(actual)
                except ArtifactError as exc:
                    isolated.update(actual='REJECTED:' + str(exc), error_type=type(exc).__name__)
                except Exception as exc:
                    isolated.update(error_type=type(exc).__name__, reason=str(exc))
                isolated['passed'] = isolated['actual'] == isolated['expected']
        if all(case['passed'] for case in evidence['cases']):
            evidence['status'] = 'PASS'
    return evidence
