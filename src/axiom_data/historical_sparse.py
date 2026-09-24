"""Historical observation plans executed through the public checkpoint collector.

Coverage means the source's declared query was exhausted, not strict historical
PIT or a fabricated daily event row. Raw leaves feed ordinary DomainCommit
builders, whose source_coverage identity retains complete empty observations.
"""
import json
from datetime import date, timedelta

from axiom_data.artifacts import ArtifactError, _digest, _json_bytes, _layout, _identity, _safe_path, load_raw_batch
from axiom_data.operations import _request, _source_binding, _check_collected, normalize_source_request

VERSION = 'historical_sparse.v1'
ENDPOINTS = ('dividend', 'forecast', 'stk_holdernumber', 'top10_holders',
             'index_member_all', 'stock_basic')


def _checkpoint_plan(plan):
    """Compare old and current collector spellings using the persisted names."""
    return dict(plan, requests_by_domain={domain: [normalize_source_request(spec) for spec in specs]
        for domain, specs in plan['requests_by_domain'].items()})


def _same_plan(left, right):
    a, b = _checkpoint_plan(left), _checkpoint_plan(right)
    a.pop('plan_digest', None); b.pop('plan_digest', None)
    return a == b


def plan_historical_sparse(*, symbols, start_session, end_session):
    from axiom_data.bootstrap_sources import plan_bootstrap_sources
    from axiom_data.consumption import validate_symbols, validate_session
    selected = validate_symbols(symbols)
    start = validate_session(start_session, 'start_session')
    end = validate_session(end_session, 'end_session')
    base = plan_bootstrap_sources(symbols=selected, start_session=start, end_session=end,
        financial_observation_start=start, benchmarks=['000300.SH'], universe_ids=['000906.SH'])
    requests = {domain: specs for domain, specs in base['requests_by_domain'].items()
                if specs[0]['endpoint'] in ENDPOINTS}
    scope = {'start': start.replace('-', ''), 'end': end.replace('-', '')}
    requests['industry_membership'] = [dict(collector='industry_qualification',
        domain='industry_membership', endpoint='index_member_all',
        params={'ts_code': symbol, 'is_new': mode}, economic_scope=scope,
        availability_policy='reference_observation') for symbol in selected for mode in ('Y', 'N')]
    # The delisted list is a historical source snapshot, not one request per day.
    requests['security_master'] = [dict(collector='market', domain='security_master',
        endpoint='stock_basic', params={'exchange': '', 'list_status': status},
        economic_scope=scope, availability_policy='reference_observation') for status in ('L', 'D', 'P')]
    for specs in requests.values():
        for spec in specs:
            _request(spec)
    plan = {'schema_version': VERSION, 'scope': {'symbols': list(selected),
        'start_session': start, 'end_session': end}, 'requests_by_domain': requests,
        'required_reused_domains': [],
        'limitations': ['stock_basic is an as-observed listing snapshot; official exchange termination Raw and boundary admission remain required',
                        'industry membership observations retain best_effort history and boundary ambiguity'],
        'ready_for_consumption': False}
    return dict(plan, plan_digest=_digest(_json_bytes(plan)))


def _check_children(parent, children):
    """A date split is an exact partition with invariant non-date selectors."""
    from axiom_data.fundamentals_source import source_date
    parent = normalize_source_request(parent)
    params = parent['params']
    if not {'start_date', 'end_date'} <= set(params) or not children:
        raise ArtifactError('sparse split requires bounded date scope')
    cursor = date.fromisoformat(source_date(params['start_date']))
    end = date.fromisoformat(source_date(params['end_date']))
    for child in children:
        _request(child)
        child = normalize_source_request(child)
        p = child['params']
        if (any(child[k] != parent[k] for k in ('collector', 'domain', 'endpoint', 'availability_policy'))
            or {k:v for k,v in p.items() if k not in {'start_date','end_date'}} !=
               {k:v for k,v in params.items() if k not in {'start_date','end_date'}}):
            raise ArtifactError('sparse child selector mismatch')
        first = date.fromisoformat(source_date(p['start_date']))
        last = date.fromisoformat(source_date(p['end_date']))
        if first != cursor or last < first or last > end:
            raise ArtifactError('sparse child interval gap or overlap')
        cursor = last + timedelta(days=1)
    if cursor != end + timedelta(days=1):
        raise ArtifactError('sparse child intervals do not exhaust parent')


def validate_sparse_coverage(data_root, *, run_id, plan, domains):
    """Rebuild coverage from current checkpoint bindings and immutable Raw bytes."""
    from axiom_data.bootstrap_sources import plan_truncated_raw_split
    return _validate_sparse_coverage(data_root, run_id=run_id, plan=plan, domains=domains,
                                     split_resolver=plan_truncated_raw_split)


def _validate_sparse_coverage(data_root, *, run_id, plan, domains, split_resolver):
    """Shared aggregate body; public callers always use the real split resolver."""
    from axiom_data.source_coverage import observation
    expected = plan_historical_sparse(**plan['scope'])
    if not _same_plan(plan, expected):
        raise ArtifactError('sparse plan differs from canonical planner')
    _identity('run_id', run_id)
    root = _layout(data_root).root
    if not domains or len(set(domains)) != len(domains) or not set(domains) <= set(plan['requests_by_domain']):
        raise ArtifactError('explicit unique sparse domains required')
    frozen = _safe_path(root, root / 'operations' / run_id / 'source_plan.json')
    if frozen.exists() and json.loads(frozen.read_bytes()) != {'plan': _checkpoint_plan(plan), 'domains': domains}:
        raise ArtifactError('sparse coverage plan binding mismatch')

    def visit(batch, spec, batch_specs, ancestors=()):
        key = _request(spec)
        _identity('run_id', batch)
        if batch in ancestors:
            raise ArtifactError('cyclic sparse checkpoint lineage')
        path = _safe_path(root, root / 'operations' / batch / 'collection.json')
        checkpoint = _safe_path(root, root / 'operations' / batch / 'collection-checkpoints' / (key.removeprefix('sha256:') + '.json'))
        record = None
        if frozen.exists() and path.exists() and checkpoint.exists():
            stored_specs = [normalize_source_request(c) for c in batch_specs]
            digest = _digest(_json_bytes({'requests': stored_specs, 'request_keys': [_request(c) for c in stored_specs]}))
            saved = json.loads(path.read_bytes())
            record = json.loads(checkpoint.read_bytes())
            if (saved['requests'] != stored_specs or saved['plan_digest'] != digest
                or record.get('schema_version') != 'collection_checkpoint.v2'
                or record.get('plan_digest') != digest or record.get('request_id') != key):
                raise ArtifactError('sparse checkpoint plan binding mismatch')
        result = {'request_id': key, 'run_id': batch, 'domain': spec['domain'],
            'request': spec, 'source': _source_binding(spec),
            'requested_interval': ({'start': spec['params']['start_date'], 'end': spec['params']['end_date']}
                                   if {'start_date', 'end_date'} <= set(spec['params']) else None),
            'economic_target_interval': spec['economic_scope'],
            'coverage_semantics': ('source_snapshot_as_observed' if spec['endpoint'] in {'stock_basic','index_member_all','dividend'} else 'requested_source_interval'),
            'status': 'NOT_QUERIED', 'complete': False, 'empty': None, 'observations': [], 'children': []}
        if record is None or record['state'] == 'PENDING':
            return result
        result['raw_batch_id'] = record['raw_batch_id']
        if record['state'] == 'VALID_COMPLETE':
            raw = load_raw_batch(root, record['raw_batch_id'])
            _check_collected(raw, spec)
            item = observation(raw)
            if not item['payload_admission'].get('complete'):
                result['status'] = 'SOURCE_UNSUPPORTED'
                return result
            admission = item['payload_admission']
            result.update(status='COMPLETE', complete=True, empty=item['empty_result'], observations=[item],
                coverage_semantics=admission.get('coverage_semantics'),
                empty_result_semantics=admission.get('empty_result_semantics'),
                checked_source_scope=admission.get('scope'))
        elif record['state'] == 'SUPERSEDED_BY_SPLIT':
            split = record['split']
            parent_raw = load_raw_batch(root, record['raw_batch_id'])
            binding = _source_binding(spec)
            if (parent_raw.manifest['domain'] != spec['domain']
                or any(parent_raw.manifest[k] != binding[k] for k in ('source_profile_version','source_profile_digest','source_profile_ref'))
                or parent_raw.manifest['request'] != {'endpoint': spec['endpoint'], 'params': spec['params'], 'fields': binding['fields']}):
                raise ArtifactError('sparse parent Raw request binding mismatch')
            if split != split_resolver(parent_raw):
                raise ArtifactError('sparse split differs from retained parent Raw')
            if record['child_refs']['request_ids'] != [_request(c) for c in split['requests']]:
                raise ArtifactError('sparse child request identity mismatch')
            _check_children(spec, split['requests'])
            child_batch = run_id + '-split-' + _digest(_json_bytes({'parent_batch': batch, 'split': split}))[7:31]
            if record['child_refs']['run_id'] != child_batch:
                raise ArtifactError('sparse child run binding mismatch')
            children = [visit(child_batch, child, split['requests'], ancestors + (batch,)) for child in split['requests']]
            result['children'] = children
            result['observations'] = [o for child in children for o in child['observations']]
            result['complete'] = all(child['complete'] for child in children)
            result['status'] = 'COMPLETE' if result['complete'] else 'PARTIAL'
            result['empty'] = all(child['empty'] for child in children) if result['complete'] else None
        elif record['state'] == 'NEEDS_SPLIT':
            result['status'] = 'TRUNCATED'
        elif (record.get('failure') or {}).get('error_type') == 'SourceCompletenessError':
            from axiom_data.source_completeness import completeness_policy
            binding = _source_binding(spec)
            policy = completeness_policy(binding['source_profile_version'], spec['endpoint'])
            if policy['status'] != 'established':
                result['status'] = 'SOURCE_UNSUPPORTED'
            elif (isinstance(policy.get('limit'), int) and not isinstance(policy['limit'], bool)
                  and policy['limit'] > 0 and record.get('raw_batch_id')
                  and len(json.loads(load_raw_batch(root, record['raw_batch_id']).payload)) >= policy['limit']):
                result['status'] = 'TRUNCATED'
            else:
                result['status'] = 'COLLECTION_FAILURE'
        else:
            result['status'] = 'COLLECTION_FAILURE'
        return result

    coverage = []
    for domain in domains:
        for offset, spec in enumerate(plan['requests_by_domain'][domain]):
            batch_offset = offset // 50 * 50
            coverage.append(visit(run_id + '-' + domain + '-' + str(batch_offset), spec,
                                  plan['requests_by_domain'][domain][batch_offset:batch_offset + 50]))
    complete = bool(coverage) and all(item['complete'] for item in coverage)
    result = {'schema_version': VERSION, 'run_id': run_id, 'status': 'COMPLETE' if complete else 'PARTIAL',
              'complete': complete, 'coverage': coverage, 'ready_for_consumption': False}
    return dict(result, coverage_digest=_digest(_json_bytes(result)))


def execute_historical_sparse(data_root, *, run_id, plan, domains=None, client=None):
    from axiom_data.bootstrap_sources import collect_bootstrap_sources
    from axiom_data.operations import _save
    domains = sorted(plan['requests_by_domain']) if domains is None else domains
    if not _same_plan(plan, plan_historical_sparse(**plan['scope'])):
        raise ArtifactError('sparse plan differs from canonical planner')
    collect_bootstrap_sources(data_root, run_id=run_id, plan=plan, domains=domains, client=client)
    result = validate_sparse_coverage(data_root, run_id=run_id, plan=plan, domains=domains)
    _save(_layout(data_root).root / 'operations' / run_id / 'sparse_coverage.json', result)
    return result
