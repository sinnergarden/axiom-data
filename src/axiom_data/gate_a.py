"""Code/source readiness and future acceptance contracts; never publish a baseline."""
import ast
import importlib
import inspect
import json
import subprocess
from datetime import date, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace

from axiom_data.artifacts import ArtifactError, _digest, _json_bytes, _validated_digest
from axiom_data.consumption import MARKET_VIEW_FIELDS, _session, _symbols
from axiom_data.domains import PR7_SNAPSHOT_DOMAINS
from axiom_data.domains.market import (
    _checked_security_identity_state, validate_security_master_rows, validate_trading_calendar_rows,
)
from axiom_data.pit import instant
from axiom_data.pr7_views import exchange_sessions


def _contract():
    return json.loads(files('axiom_data.scope').joinpath('pr8_gate_a.v2.json').read_bytes())


def code_identity():
    """Bind installed executable/config bytes, including uncommitted changes."""
    root = Path(__file__).parent
    paths = sorted(p for p in root.rglob('*') if p.is_file() and p.suffix in {'.py', '.json'})
    projection = {p.relative_to(root).as_posix(): _digest(p.read_bytes()) for p in paths}
    revision = None
    try:
        revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root,
                                  capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pass
    return {'code_revision': revision, 'implementation_digest': _digest(_json_bytes(projection)),
            'files': projection, 'validator_version': 'gate_a_readiness.v2'}


def _registry():
    content = files('axiom_data.scope').joinpath('pr7_scope.v1.json').read_bytes()
    return json.loads(content), _digest(content)


def _profile(version):
    return json.loads(files('axiom_data.source_profiles').joinpath(version + '.json').read_bytes())


def completeness_matrix():
    """One row per required source binding, with every endpoint's actual policy."""
    from axiom_data.source_completeness import validate_policy_contract
    contract = _contract()
    rows = []
    for source, spec in contract['sources'].items():
        if source not in contract['initial_completeness_gaps']:
            continue
        policies = {endpoint: validate_policy_contract(spec['profile'], endpoint) for endpoint in spec['endpoints']}
        rows.append({'source': source, 'domain': spec['domain'], 'profile': spec['profile'],
                     'status': 'PASS' if all(p['status'] == 'PASS' for p in policies.values()) else 'BLOCKED',
                     'endpoints': policies,
                     'requirements': sorted(leaf for leaf, binding in contract['requirements'].items()
                                            if source in binding['sources']),
                     'coverage_policy': spec['coverage'], 'pit_policy': spec['pit'],
                     'source_semantics': spec['source_semantics']})
    if len(rows) != 13:
        raise ArtifactError('original 13 source completeness bindings changed')
    return rows


def _entry(name):
    module, member = name.rsplit('.', 1)
    value = getattr(importlib.import_module(module), member)
    if not callable(value) or not inspect.isfunction(value):
        raise ArtifactError('executable Python entry required: ' + name)
    return value


def _scope(scope):
    keys = {'symbols', 'start_session', 'end_session', 'financial_observation_start', 'benchmarks', 'universe_ids'}
    if not isinstance(scope, dict) or set(scope) != keys:
        raise ArtifactError('complete explicit source scope required')
    scope = dict(scope)
    for key in ('symbols', 'benchmarks', 'universe_ids'):
        scope[key] = list(_symbols(scope[key]))
    for key in ('start_session', 'end_session', 'financial_observation_start'):
        scope[key] = _session(scope[key], key)
    if not scope['financial_observation_start'] <= scope['start_session'] <= scope['end_session']:
        raise ArtifactError('source scope or financial lookback reversed')
    return scope


def make_gate_a_plan(scope):
    """Make a full requested-scope plan without source I/O or future artifact IDs."""
    scope = _scope(scope)
    contract = _contract()
    return {'schema_version': 'gate_a_plan.v1', 'scope': scope,
            'scope_registry_digest': contract['scope_registry_digest'],
            'historical_view_policy': contract['historical_view_policy'],
            'terminal_evidence_plan': {
                'schema_version': 'gate_b_evidence_plan.v1',
                'target_digest': _digest(_json_bytes(scope)),
                'scope_registry_digest': contract['scope_registry_digest'],
                'requirements': contract['terminal_evidence'],
                'daily_cases': contract['daily_cases']}}


def plan_reference_sources(*, scope):
    """Bind reused reference coverage to real validators, without recollecting it."""
    scope = _scope(scope)
    contract = _contract()
    return [{'domain': contract['sources'][key]['domain'],
             'profile': contract['sources'][key]['profile'],
             'endpoints': contract['sources'][key]['endpoints'],
             'scope': scope, 'mode': 'reuse_validated',
             'required_refs': ['domain_commit_id', 'manifest_digest'],
             'validator': 'axiom_data.artifacts.validate_domain_commit_closure'}
            for key in contract['reference_sources']]


def _intervals_cover(intervals, first, last):
    cursor = date.fromisoformat(first)
    stop = date.fromisoformat(last)
    for lower, upper in sorted(intervals):
        lower = date.fromisoformat(_session(lower, 'request start'))
        upper = date.fromisoformat(_session(upper, 'request end'))
        if lower > upper or lower > cursor:
            return False
        if upper >= cursor:
            if upper >= stop:
                return True
            cursor = upper + timedelta(days=1)
    return False


def _request_coverage(requests, spec, scope):
    """Check planned request unions, not row extents or supplier completeness."""
    selectors = scope['benchmarks'] if spec['domain'] == 'benchmark_daily' else (
        scope['universe_ids'] if spec['domain'] == 'universe_membership' else scope['symbols'])
    lower = scope['financial_observation_start'] if spec['domain'] == 'financial_events' else scope['start_session']
    grouped = {}
    for request in requests:
        if request['domain'] == spec['domain'] and request['collector'] == spec['collector']:
            params = request['params']
            grouped.setdefault((request['endpoint'], params.get('ts_code', params.get('index_code'))), []).append(request)
    for endpoint in spec['endpoints']:
        for symbol in selectors:
            matching = grouped.get((endpoint, symbol), [])
            if endpoint == 'dividend':
                if not any(r['params'] == {'ts_code': symbol} for r in matching):
                    raise ArtifactError('security-wide dividend observation absent')
                continue
            intervals = [(datetime.strptime(p['start_date'], '%Y%m%d').date().isoformat(),
                          datetime.strptime(p['end_date'], '%Y%m%d').date().isoformat())
                         for p in (r['params'] for r in matching)]
            if not _intervals_cover(intervals, lower, scope['end_session']):
                raise ArtifactError('planner misses ' + spec['domain'] + '/' + endpoint + '/' + symbol)


def _source_plan(scope, contract):
    from axiom_data.operations import _request
    generated = _entry('axiom_data.bootstrap_sources.plan_bootstrap_sources')(**scope)
    domains = {s['domain'] for s in contract['sources'].values() if s['mode'] == 'collect'}
    reused = {s['domain'] for s in contract['sources'].values() if s['mode'] == 'reuse'}
    if (generated.get('schema_version') != 'v1_bootstrap_sources.v1' or generated.get('scope') != scope
            or set(generated.get('requests_by_domain', {})) != domains
            or set(generated.get('required_reused_domains', [])) != reused):
        raise ArtifactError('source planner changed the requested scope or domain composition')
    ids = set()
    allowed = {(s['domain'], s['collector'], endpoint) for s in contract['sources'].values()
               if s['mode'] == 'collect' for endpoint in s['endpoints']}
    for domain, requests in generated['requests_by_domain'].items():
        for spec in requests:
            identity = _request(spec)
            if identity in ids or (domain, spec['collector'], spec['endpoint']) not in allowed or spec['domain'] != domain:
                raise ArtifactError('duplicate or unregistered source planner request')
            ids.add(identity)
            params = spec['params']
            selectors = scope['benchmarks'] if domain == 'benchmark_daily' else (
                scope['universe_ids'] if domain == 'universe_membership' else scope['symbols'])
            if params.get('ts_code', params.get('index_code')) not in selectors:
                raise ArtifactError('source planner request outside security/index target')
            if 'start_date' in params:
                first = datetime.strptime(params['start_date'], '%Y%m%d').date().isoformat()
                last = datetime.strptime(params['end_date'], '%Y%m%d').date().isoformat()
                lower = scope['financial_observation_start'] if domain == 'financial_events' else scope['start_session']
                if not lower <= first <= last <= scope['end_session']:
                    raise ArtifactError('source planner request outside time target')
    return generated


def _operational_routes():
    """Check the existing explicit service call paths, recording their actual code."""
    routes = {
        'axiom_data.operations.bootstrap': {'assemble_candidate', 'create_snapshot'},
        'axiom_data.operations.daily': {'collect_requests', '_validate_domain_inputs', 'assemble_candidate'},
        'axiom_data.operations.repair': {'assemble_candidate'},
        'axiom_data.operations.assemble_candidate': {'_validate_domain_inputs', 'BuildApplication'},
        'axiom_data.operations.inspect_scope': {'session_coverage'},
        'axiom_data.artifacts.rebuild_catalog': {'_catalog_entries'},
    }
    result = {}
    for name, required in routes.items():
        source = inspect.getsource(_entry(name))
        tree = ast.parse(source)
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        if not required <= calls:
            raise ArtifactError('public operation admission/build route absent: ' + name)
        result[name] = {'code_digest': _digest(source.encode()), 'required_calls': sorted(required)}
    # The reviewed bodies pin actual executable paths. A call hidden in dead
    # code or moved after publication cannot pass merely by retaining its name.
    expected = _contract()['reviewed_admission_routes']
    actual = admission_route_identity()
    if actual != expected:
        raise ArtifactError('public admission route differs from reviewed executable binding')
    result['reviewed_binding'] = actual
    return result


def admission_route_identity():
    from axiom_data import operations, artifacts, build, source_coverage, source_completeness
    entries = {name: getattr(operations, name) for name in (
        'bootstrap', 'daily', 'repair', 'assemble_candidate', 'collect_requests',
        '_check_collected', '_checked_collection_record', '_requalify_sources', 'inspect_scope', 'inspect_snapshot')}
    entries.update({
        'BuildApplication.build': build.BuildApplication.build,
        'MarketDomainBuilder.__call__': artifacts.MarketDomainBuilder.__call__,
        'create_snapshot': artifacts.create_snapshot,
        'closure': artifacts._validated_domain_commit_with_raw_closure,
        'coverage_observation': source_coverage.observation,
        'raw_admission': source_completeness.validate_raw_completeness,
        'raw_admission_dispatch': source_completeness._validate_raw_completeness,
        'payload_admission': source_completeness.validate_payload_completeness,
        'payload_admission_dispatch': source_completeness._validate_payload_completeness,
        'policy_validation': source_completeness.validate_policy_contract,
        'policy_rules': source_completeness._policy_issues,
        'raw_binding': source_completeness._validate_raw_binding,
        'payload_scope': source_completeness._validate_scoped_response,
        'transport_result': source_completeness._validate_raw_result})
    for name in ('axiom_data.bootstrap_sources.collect_bootstrap_sources',
                 'axiom_data.contracts.require_writable_contract',
                 'axiom_data.contracts.writable_contracts',
                 'axiom_data.verification_cache.candidate_verification',
                 'axiom_data.verification_cache.current_source_cache',
                 'axiom_data.view_operation.materialize_views',
                 'axiom_data.admission_plan._config'):
        entries[name] = _entry(name)
    from axiom_data.admission_plan import _builders
    entries.update({'view_builder:' + name: function for name, function in _builders().items()})
    return {name: _digest(inspect.getsource(function).encode()) for name, function in entries.items()}


def _sparse_readiness():
    from axiom_data import historical_sparse
    from axiom_data.sparse_conformance import run_sparse_conformance
    required = {'dividend', 'forecast', 'stk_holdernumber', 'top10_holders', 'index_member_all', 'stock_basic'}
    if not required <= set(historical_sparse.ENDPOINTS):
        raise ArtifactError('required historical sparse endpoint executor missing')
    plan = historical_sparse.plan_historical_sparse(
        symbols=['600036.SH'], start_session='2014-01-01', end_session='2026-09-08')
    specs = [r for group in plan['requests_by_domain'].values() for r in group]
    if not required <= {r['endpoint'] for r in specs} or len(specs) > 100:
        raise ArtifactError('sparse planner misses a source or expands into daily scans')
    return {'version': historical_sparse.VERSION, 'plan_digest': plan['plan_digest'],
            'endpoint_coverage': sorted(required), 'request_count': len(specs),
            'execution_module_digest': _digest(Path(historical_sparse.__file__).read_bytes()),
            'behavioral_conformance': run_sparse_conformance(),
            'evidence_kind': 'offline_behavior_not_historical_source_availability'}


def _writable_readiness():
    from axiom_data.contracts import writable_contracts
    policy = writable_contracts()
    digest = _digest(_json_bytes(policy))
    if digest != _contract()['writable_contracts_digest']:
        raise ArtifactError('writable contract policy differs from reviewed version')
    return {'policy': policy, 'policy_digest': digest,
            'publication_paths': ['BuildApplication.build', 'MarketDomainBuilder.__call__',
                                  'operations._validate_domain_inputs'],
            'legacy_semantics': 'read_only; contract_upgrade_requires_new_lineage'}


def _reference_qualification(scope):
    """Reuse the previously reviewed source decision through exact evidence refs."""
    root = Path(__file__).resolve().parents[2]
    refs = _contract()['reference_qualification']
    records = {}
    for name, ref in refs.items():
        path = root / ref['path']
        if root not in path.resolve().parents:
            raise ArtifactError('reference evidence outside repository')
        content = path.read_bytes()
        if _digest(content) != ref['content_digest']:
            raise ArtifactError('reviewed reference evidence digest mismatch')
        records[name] = json.loads(content)
    target = records['target']['scope']
    symbols = target['symbols']
    industry = records['industry']; identity = records['identity']
    if (len(symbols) != 3601 or len(set(symbols)) != 3601 or not set(scope['symbols']) <= set(symbols)
            or identity.get('status') != 'PASS' or identity.get('rows') != 3601
            or industry.get('status') != 'PASS' or industry['proof']['automatic_conditions'] != [True] * 4
            or industry['profile'] != _profile('tushare_sw2021.v1')):
        raise ArtifactError('reviewed target/security/industry qualification differs')
    return {'evidence_refs': refs, 'target_security_count': 3601,
            'requested_security_count': len(scope['symbols']),
            'security_commit_id': identity['security_commit_id'],
            'industry_commit_id': identity['industry_commit_id'],
            'authority': 'SW2021', 'automatic_anomaly_conditions': [True] * 4,
            'evidence_kind': 'previously_reviewed_source_qualification_not_new_bulk_admission'}


def _requirement_bindings(contract, registry):
    """Bind the registry to the actual public exporters, including derived owners."""
    from axiom_data.pr6_views import FIELD_MAP, WIDE_FIELDS
    from axiom_data.pr7_views import LEAF_DOMAINS
    expected = {leaf: (spec['domain'], spec['field']) for leaf, spec in registry['pr5_public_evidence'].items()}
    for leaf in WIDE_FIELDS:
        domain = ('financial_stable_derived' if leaf.startswith('financial.') else
                  'financial_events' if leaf in FIELD_MAP else
                  {'valuation': 'valuation_daily', 'industry': 'industry_membership',
                   'universe': 'universe_membership'}[leaf.split('.')[0]])
        expected[leaf] = (domain, leaf)
    expected.update({leaf: (domain, leaf) for leaf, domain in LEAF_DOMAINS.items()})
    if set(expected) != set(contract['requirements']):
        raise ArtifactError('actual public exporters do not resolve the frozen requirements')
    for leaf, (domain, field) in expected.items():
        spec = contract['requirements'][leaf]
        if (spec.get('domain'), spec.get('public_field')) != (domain, field):
            raise ArtifactError('requirement public domain/field substituted: ' + leaf)
        source_domains = {contract['sources'][s]['domain'] for s in spec['sources']}
        required = {'market_daily', 'adjustment_factors'} if domain == 'adjusted_price' else {
            'financial_events' if domain == 'financial_stable_derived' else domain}
        if not required <= source_domains:
            raise ArtifactError('requirement source does not back public field: ' + leaf)


def _coverage_readiness(contract):
    """Exercise the pure versioned projection/state API; no Raw is published."""
    from axiom_data import source_coverage
    from axiom_data.artifacts import RawBatch, RawBatchRef
    expected = contract['coverage_policy']
    if (source_coverage.POLICY != expected['version']
            or _entry(expected['projector']) is not source_coverage.observation
            or _entry(expected['state']) is not source_coverage.state):
        raise ArtifactError('source coverage policy/entry mismatch')
    profile = _profile('tushare_fina_indicator.v1')
    payload = b'[]'
    manifest = {'schema_version': 'raw_batch.v2', 'status': 'success', 'domain': 'financial_events',
                'source_profile_ref': 'tushare.pr6.fina_indicator',
                'source_profile_version': profile['profile_version'],
                'source_profile_digest': _digest(_json_bytes(profile)),
                'request': {'endpoint': 'fina_indicator', 'params': {
                    'ts_code': '600036.SH', 'start_date': '20250101', 'end_date': '20250331'},
                    'fields': profile['endpoints']['fina_indicator']['fields']},
                'retrieved_at': '2026-01-01T00:00:00Z',
                'payload_files': [{'path': 'payload.json', 'content_digest': _digest(payload)}],
                'summary': {'rows': 0}}
    raw = RawBatch(RawBatchRef('gate-a-code-canary-1', _digest(_json_bytes(manifest))), manifest, payload)
    projected = source_coverage.observation(raw)
    required = {'raw_ref', 'source_profile', 'source_profile_version', 'source_profile_digest',
                'request', 'observed_at', 'result_status', 'payload_admission', 'row_count', 'empty_result'}
    if (set(projected) != required or projected['request'] != manifest['request']
            or projected['observed_at'] != manifest['retrieved_at'] or projected['row_count'] != 0
            or projected['empty_result'] is not True or projected['payload_admission'].get('complete') is not True):
        raise ArtifactError('coverage projection loses source/request/completeness semantics')
    initial = source_coverage.state(None, set(), [projected])
    parent = SimpleNamespace(manifest={'source_coverage': initial})
    replay = source_coverage.state(parent, {raw.ref.raw_batch_id}, [projected])
    expanded = dict(projected, raw_ref=dict(projected['raw_ref'], raw_batch_id='gate-a-code-canary-2'),
                    observed_at='2026-01-02T00:00:00Z')
    extended = source_coverage.state(parent, {raw.ref.raw_batch_id}, [expanded])
    if (initial['schema_version'] != expected['version'] or replay['state_digest'] != initial['state_digest']
            or extended['state_digest'] == initial['state_digest']):
        raise ArtifactError('coverage state loses idempotence or new empty observation identity')
    partial = RawBatch(raw.ref, dict(manifest, summary={'rows': 0, 'complete': False}), payload)
    try:
        source_coverage.observation(partial)
    except ArtifactError:
        pass
    else:
        raise ArtifactError('partial observation accepted by coverage policy')
    source = inspect.getsource(_entry('axiom_data.operations.assemble_candidate'))
    config = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.keyword)
              and node.arg == 'coverage_state_policy' and isinstance(node.value, ast.Constant)
              and node.value.value == expected['version']]
    if not config:
        raise ArtifactError('public assembly does not enable the coverage identity policy')
    return {'version': expected['version'], 'projector_code_digest': _digest(inspect.getsource(source_coverage.observation).encode()),
            'state_code_digest': _digest(inspect.getsource(source_coverage.state).encode()),
            'checks': ['synthetic_empty_projection', 'exact_replay', 'new_observation_identity', 'partial_rejection'],
            'evidence_kind': 'code_canary_not_source_availability_evidence'}


def validate_terminal_evidence_plan(plan):
    """Validate future shapes and executable entry availability, without artifacts."""
    contract = _contract()
    if (not isinstance(plan, dict) or set(plan) != {'schema_version', 'target_digest', 'scope_registry_digest', 'requirements', 'daily_cases'}
            or plan['schema_version'] != 'gate_b_evidence_plan.v1'
            or plan['requirements'] != contract['terminal_evidence']
            or plan['daily_cases'] != contract['daily_cases']
            or plan['scope_registry_digest'] != _registry()[1]):
        raise ArtifactError('terminal evidence requirements differ from frozen contract')
    _validated_digest('target_digest', plan['target_digest'])
    for category, spec in plan['requirements'].items():
        if (set(spec) != {'required', 'producer', 'validator', 'calls'}
                or not isinstance(spec['required'], list) or not spec['required']
                or len(set(spec['required'])) != len(spec['required'])
                or any(not isinstance(field, str) or not field for field in spec['required'])
                or set(spec['calls']) != {'producer', 'validator'}):
            raise ArtifactError('terminal artifact/ref schema invalid: ' + category)
        for role in ('producer', 'validator'):
            call = spec['calls'][role]
            if (not isinstance(spec[role], str) or not spec[role].startswith('axiom_data.')
                    or set(call) != {'args', 'kwargs'} or not isinstance(call['args'], list)
                    or not isinstance(call['kwargs'], dict)):
                raise ArtifactError('terminal evidence call schema invalid: ' + category)
    missing = []
    entries = {}
    for category, spec in sorted(plan['requirements'].items()):
        for role in ('producer', 'validator'):
            name = spec[role]
            try:
                function = _entry(name)
                call = spec['calls'][role]
                inspect.signature(function).bind(*call['args'], **call['kwargs'])
                entries[name] = {'signature': str(inspect.signature(function)),
                                 'code_digest': _digest(inspect.getsource(function).encode())}
            except (ImportError, AttributeError, TypeError, OSError, SyntaxError, ArtifactError):
                missing.append({'category': category, 'role': role, 'entry': name})
    return {'schema_version': 'terminal_evidence_plan_check.v1',
            'status': 'PLAN_DEFINED' if not missing else 'CAPABILITY_BLOCKED',
            'schema_status': 'PLAN_DEFINED',
            'execution_status': 'CAPABILITY_READY' if not missing else 'CAPABILITY_BLOCKED',
            'missing_capabilities': missing, 'entries': entries,
            'plan_digest': _digest(_json_bytes(plan)), 'gate_b_status': 'NOT_ASSESSED'}


def validate_gate_a(plan):
    """Read-only readiness; even READY requires a separate independent review."""
    findings = []
    evidence = {'code_identity': code_identity()}
    def check(section, operation):
        try:
            evidence[section] = operation()
        except (ArtifactError, ImportError, AttributeError, KeyError, TypeError, ValueError, OSError, SyntaxError) as exc:
            findings.append({'section': section, 'reason': str(exc), 'error_type': type(exc).__name__})
    try:
        contract = _contract()
        if not isinstance(plan, dict) or set(plan) != set(make_gate_a_plan(plan.get('scope', {}))):
            raise ArtifactError('complete Gate A plan required')
        scope = _scope(plan['scope'])
        if plan != make_gate_a_plan(scope):
            raise ArtifactError('Gate A plan changes frozen requirements or target binding')
        registry, digest = _registry()
        leaves = set().union(*(set(v) for v in registry['partition'].values()))
        if (digest != contract['scope_registry_digest'] or set(contract['requirements']) != leaves
                or len(leaves) != 56 or len(registry['feature_dependencies']) != 469
                or any(not set(f['leaves']) <= leaves for f in registry['feature_dependencies'])):
            raise ArtifactError('frozen 56/469 registry binding mismatch')
        if {s['domain'] for s in contract['sources'].values()} != set(PR7_SNAPSHOT_DOMAINS):
            raise ArtifactError('source registry lacks an accepted canonical domain')
        for leaf, spec in contract['requirements'].items():
            if not spec['sources'] or not set(spec['sources']) <= set(contract['sources']):
                raise ArtifactError('requirement has no source binding: ' + leaf)
        _requirement_bindings(contract, registry)
        evidence['scope_registry_digest'] = digest
        evidence['contract_digest'] = _digest(_json_bytes(contract))
        evidence['requirement_bindings'] = contract['requirements']
        evidence['requested_target_digest'] = _digest(_json_bytes(scope))
    except (ArtifactError, KeyError, TypeError, ValueError, OSError) as exc:
        return _gate_result([{'section': 'plan', 'reason': str(exc)}], evidence)
    check('source_plan', lambda: _source_plan(scope, contract))
    check('reference_plan', lambda: plan_reference_sources(scope=scope))
    for name, spec in contract['sources'].items():
        def source_check(spec=spec):
            from axiom_data.source_completeness import validate_policy_contract
            profile = _profile(spec['profile'])
            if profile['profile_version'] != spec['profile'] or not set(spec['endpoints']) <= set(profile.get('endpoints', {})):
                raise ArtifactError('required source profile/endpoint absent')
            semantics = spec['source_semantics']
            for key, value in semantics.items():
                if key == 'endpoints':
                    for endpoint, definition in value.items():
                        if any(profile['endpoints'][endpoint].get(k) != v for k, v in definition.items()):
                            raise ArtifactError('source endpoint coverage/PIT semantics changed')
                elif profile.get(key) != value:
                    raise ArtifactError('source coverage/PIT semantics changed')
            if (spec['coverage'] != contract['coverage_policy']['version']
                    or spec['pit'] != 'best_effort_terminal_history; observation_time_is_not_historical_publication'):
                raise ArtifactError('unreviewed source coverage/PIT policy binding')
            policies = {}
            for endpoint in spec['endpoints']:
                checked = validate_policy_contract(spec['profile'], endpoint)
                if checked.get('status') != 'PASS':
                    raise ArtifactError('source completeness policy unestablished: ' + endpoint + ': ' +
                                        '; '.join(checked.get('reasons', [])))
                policies[endpoint] = checked
            planner = _entry(spec['planner'])
            if spec['mode'] == 'collect':
                if planner is not _entry('axiom_data.bootstrap_sources.plan_bootstrap_sources'):
                    raise ArtifactError('unreviewed source planner')
                generated = evidence.get('source_plan')
                if generated is None:
                    raise ArtifactError('source planner failed')
                _request_coverage(generated['requests_by_domain'].get(spec['domain'], []), spec, scope)
            return {'profile_digest': _digest(_json_bytes(profile)), 'policies': policies,
                    'coverage_policy': spec['coverage'], 'pit_policy': spec['pit'],
                    'source_semantics_digest': _digest(_json_bytes(semantics))}
        check('source:' + name, source_check)
    def industry_check():
        profile = _profile('tushare_sw2021.v1')
        boundary = _profile('exchange_security.v1')
        anomaly = profile.get('anomaly_mappings', [])
        if (profile.get('classification_system') != 'SW2021' or profile.get('historical_availability') != 'best_effort'
                or profile.get('gap_policy') != 'classification_unavailable/source_coverage_gap'
                or profile.get('out_date_policy') != 'boundary_session_ambiguous'
                or len(anomaly) != 1 or anomaly[0].get('source_code') != '850401.SI'
                or anomaly[0].get('canonical_code') != '850412.SI'
                or 'exclusive identity end' not in boundary.get('boundary', '')):
            raise ArtifactError('industry/security authority policy differs from accepted decisions')
        return {'industry_profile_digest': _digest(_json_bytes(profile)),
                'boundary_profile_digest': _digest(_json_bytes(boundary))}
    check('industry', industry_check)
    check('reference_qualification', lambda: _reference_qualification(scope))
    check('coverage_policy', lambda: _coverage_readiness(contract))
    check('public_routes', _operational_routes)
    check('historical_executor', lambda: {
        'entry': 'axiom_data.gate_a.plan_historical_views',
        'code_digest': _digest(inspect.getsource(_entry('axiom_data.gate_a.plan_historical_views')).encode()),
        'policy': contract['historical_view_policy']})
    check('terminal_plan', lambda: validate_terminal_evidence_plan(plan['terminal_evidence_plan']))
    # Gate A checks the frozen future artifact/ref contract. The missing Gate B
    # executors remain visible and still block validate_terminal_evidence.
    deferred = {('full_admission', 'producer'), ('full_admission', 'validator'),
                ('daily', 'validator'), ('notebook', 'producer'), ('notebook', 'validator')}
    missing = [entry for entry in evidence.get('terminal_plan', {}).get('missing_capabilities', [])
               if (entry['category'], entry['role']) not in deferred]
    if missing:
        findings.append({'section': 'execution_entries', 'reason': 'required bulk execution entry missing',
                         'missing': missing})
    check('historical_sparse', _sparse_readiness)
    behavior = evidence.get('historical_sparse', {}).get('behavioral_conformance', {})
    if behavior.get('status') != 'PASS':
        findings.append({'section': 'historical_sparse',
                         'reason': 'sparse behavioral conformance did not pass'})
    check('writable_contracts', _writable_readiness)
    generated = evidence.get('source_plan')
    if generated is not None:
        evidence['source_plan'] = {
            'schema_version': generated['schema_version'], 'scope': generated['scope'],
            'plan_digest': _digest(_json_bytes(generated)),
            'required_reused_domains': generated['required_reused_domains'],
            'domains': {domain: {'request_count': len(requests),
                                'request_plan_digest': _digest(_json_bytes(requests)),
                                'endpoints': sorted({r['endpoint'] for r in requests})}
                        for domain, requests in generated['requests_by_domain'].items()}}
    return _gate_result(findings, evidence)


def _gate_result(findings, evidence):
    result = {'schema_version': 'gate_a_readiness.v2',
            'status': 'GATE_A_BLOCKED' if findings else 'GATE_A_READY_FOR_BULK_BUILD',
            'findings': findings, 'evidence': evidence, 'external_review_required': True,
            'bulk_authorized': False, 'gate_b_status': 'NOT_ASSESSED', 'ready_for_consumption': False}
    return dict(result, report_digest=_digest(_json_bytes(result)))


def validate_gate_a_report(report, *, plan):
    """An earlier PASS is usable only under the same code, policies and plan."""
    if not isinstance(report, dict):
        raise ArtifactError('Gate A report required')
    content = {k: v for k, v in report.items() if k != 'report_digest'}
    if report.get('report_digest') != _digest(_json_bytes(content)):
        raise ArtifactError('Gate A report digest mismatch')
    if report != validate_gate_a(plan):
        raise ArtifactError('Gate A report no longer matches current code/contracts/plan')
    return report['status']


def plan_historical_views(*, target, security_rows, calendar_rows, universe_ids, knowledge_cutoff):
    """Per-security last eligible anchor; explicit geometry, never fill source gaps."""
    if not isinstance(target, dict) or set(target) != {'symbols', 'start_session', 'end_session'}:
        raise ArtifactError('complete historical target required')
    target = dict(target, symbols=list(_symbols(target['symbols'])),
                  start_session=_session(target['start_session'], 'start_session'),
                  end_session=_session(target['end_session'], 'end_session'))
    groups = list(_symbols(universe_ids))
    cutoff = instant(knowledge_cutoff).isoformat()
    security_rows = list(security_rows)
    calendar_rows = list(calendar_rows)
    validate_security_master_rows(security_rows)
    validate_trading_calendar_rows(calendar_rows)
    reader = SimpleNamespace(security_master=lambda: security_rows, trading_calendar=lambda: calendar_rows)
    calendars = exchange_sessions(reader, target['symbols'], target['start_session'], target['end_session'])
    identities = {r['symbol']: r for r in security_rows}
    views = {}; gaps = []; expected = {}
    for symbol, calendar in calendars.items():
        states = {d: _checked_security_identity_state(identities[symbol], d) for d in calendar['sessions']}
        eligible = [d for d, state in states.items() if state == 'within_identity_interval']
        expected[symbol] = eligible
        excluded = {state: [d for d, s in states.items() if s == state]
                    for state in sorted(set(states.values()) - {'within_identity_interval'})}
        for reason, sessions in excluded.items():
            gaps.append({'symbol': symbol, 'reason': reason, 'sessions': sessions,
                         'qualification': 'IDENTITY_ONLY' if reason != 'unknown' else 'BLOCKED'})
        if not eligible:
            gaps.append({'symbol': symbol, 'reason': 'no_eligible_target_session', 'sessions': [], 'qualification': 'BLOCKED'})
            continue
        common = {'symbols': [symbol], 'start_session': eligible[0], 'end_session': eligible[-1]}
        configs = {
            'adjusted_price': dict(common, anchor_session=eligible[-1], decision_cutoff=eligible[-1], pit_policy='research_non_pit'),
            'market_replay': common,
            'market_qlib': dict(common, fields=list(MARKET_VIEW_FIELDS), price_basis='unadjusted', pit_policy='best_effort'),
            'pr6_fact': dict(common, universe_ids=groups, industry_system='SW2021', pit_policy='best_effort_vendor_v1', knowledge_cutoff=cutoff),
            'pr7_fact': dict(common, pit_policy='best_effort_vendor_v1', knowledge_cutoff=cutoff),
        }
        for kind, config in configs.items():
            from axiom_data.admission_plan import _builders, _config
            _config(_builders()[kind], 'FUTURE_VALIDATED_ROOT', 'FUTURE_EXPLICIT_SNAPSHOT', config)
            views[kind + '-' + symbol] = {'kind': kind, 'config': config}
    return {'schema_version': 'historical_view_execution_plan.v1',
            'status': 'GEOMETRY_BLOCKED' if any(g['qualification'] == 'BLOCKED' for g in gaps) else 'GEOMETRY_DEFINED',
            'target': target, 'geometry_digest': _digest(_json_bytes(target)),
            'expected_sessions': expected, 'identity_exclusions': gaps, 'views': views,
            'execution_entry': 'axiom_data.view_operation.materialize_views',
            'source_availability_validation': 'PENDING', 'view_payload_validation': 'PENDING',
            'full_admission': 'PENDING', 'ready_for_consumption': False}


def validate_terminal_evidence(data_root, evidence, *, plan):
    """Reject unbound or incomplete terminal claims; full admission stays external.

    This validates provided evidence, never executes daily/recovery/Notebook work.
    A complete report still requires the independent Gate B reviewer decision.
    """
    checked = validate_terminal_evidence_plan(plan)
    required = plan['requirements']
    if not isinstance(evidence, dict) or set(evidence) != set(required):
        raise ArtifactError('all terminal evidence categories required')
    for category, spec in required.items():
        record = evidence[category]
        if not isinstance(record, dict) or not set(spec['required']) <= set(record):
            raise ArtifactError('terminal evidence fields missing: ' + category)
    if checked['missing_capabilities']:
        return {'status': 'EVIDENCE_BLOCKED', 'missing_capabilities': checked['missing_capabilities'],
                'gate_b_status': 'NOT_ASSESSED', 'ready_for_consumption': False}
    from axiom_data.artifacts import load_snapshot, _layout, _safe_path
    from axiom_data.recovery import _view_loaders
    root = _layout(data_root).root
    baseline = evidence['baseline']; snapshot_id = baseline['snapshot_id']
    snapshot = load_snapshot(root, snapshot_id)
    if snapshot.ref.manifest_digest != baseline['manifest_digest'] or baseline['target_digest'] != plan['target_digest']:
        raise ArtifactError('terminal baseline/target binding mismatch')
    views = evidence['views']
    if views['snapshot_id'] != snapshot_id or views['target_digest'] != plan['target_digest'] or not isinstance(views['refs'], dict) or not views['refs']:
        raise ArtifactError('terminal View target mismatch')
    kinds = set()
    for spec in views['refs'].values():
        if not isinstance(spec, dict) or set(spec) != {'kind', 'view_id', 'manifest_digest'}:
            raise ArtifactError('explicit terminal View refs required')
        loader = _view_loaders().get(spec['kind'])
        if loader is None:
            raise ArtifactError('unknown terminal View kind')
        view = loader(root, spec['view_id'])
        if view.ref.manifest_digest != spec['manifest_digest'] or view.manifest['snapshot_ref']['snapshot_id'] != snapshot_id:
            raise ArtifactError('terminal View identity mismatch')
        kinds.add(spec['kind'])
    if kinds != set(_contract()['historical_view_policy']['view_kinds']):
        raise ArtifactError('required terminal View family missing')
    admission = evidence['full_admission']
    if (admission['snapshot_id'] != snapshot_id or admission['target_digest'] != plan['target_digest']
            or admission['scope_registry_digest'] != plan['scope_registry_digest']):
        raise ArtifactError('full admission baseline/scope mismatch')
    checked_admission = _entry(required['full_admission']['validator'])(
        root, admission, expected_views=views['refs'], target_digest=plan['target_digest'])
    if (not isinstance(checked_admission, dict) or checked_admission.get('status') != 'FULL_ADMISSION_VALIDATED'
            or checked_admission.get('snapshot_id') != snapshot_id
            or checked_admission.get('target_digest') != plan['target_digest']):
        raise ArtifactError('full admission verifier did not validate the declared target')
    daily = evidence['daily']
    if daily['baseline_snapshot_id'] != snapshot_id or set(daily['cases']) != set(plan['daily_cases']):
        raise ArtifactError('daily case coverage mismatch')
    if len({ref.get('run_id') for ref in daily['cases'].values()}) != len(plan['daily_cases']):
        raise ArtifactError('distinct daily case executions required')
    for case, ref in daily['cases'].items():
        if not isinstance(ref, dict) or set(ref) != {'run_id', 'content_digest'}:
            raise ArtifactError('daily execution record ref required')
        from axiom_data.artifacts import _identity
        run_id = _identity('run_id', ref['run_id'])
        content = _safe_path(root, root / 'operations' / run_id / 'daily.json').read_bytes()
        record = json.loads(content)
        if (_digest(content) != ref['content_digest'] or record['plan']['source_plan']['parent_snapshot_id'] != snapshot_id
                or record['status'] not in {'NO_CHANGE', 'CANDIDATE_BUILT'}):
            raise ArtifactError('daily execution evidence mismatch')
        result = load_snapshot(root, record['snapshot_id'])
        if case == 'no_change' and (record['status'] != 'NO_CHANGE' or result.ref.snapshot_id != snapshot_id):
            raise ArtifactError('daily no-change evidence differs')
    daily_check = _entry(required['daily']['validator'])(root, daily, expected_baseline=snapshot_id)
    if not isinstance(daily_check, dict) or daily_check.get('status') != 'DAILY_EVIDENCE_VALIDATED':
        raise ArtifactError('daily scenario semantics have not been validated')
    recovery = evidence['recovery']
    if (recovery['snapshot_id'] != snapshot_id or recovery['snapshot_manifest_digest'] != baseline['manifest_digest']
            or recovery['blocked_external_attempts'] != 0
            or Path(recovery['restored_root']).resolve() == root.resolve()):
        raise ArtifactError('offline recovery binding mismatch')
    # Recovery's successful run must be supplied with a durable, hash-bound report.
    for field in ('run_id', 'content_digest'):
        if field not in recovery:
            raise ArtifactError('recovery execution record ref missing')
    restored = _layout(recovery['restored_root']).root
    content = _safe_path(restored, restored / 'operations' / _identity('run_id', recovery['run_id']) / 'recovery.json').read_bytes()
    record = json.loads(content)
    if (_digest(content) != recovery['content_digest'] or record['status'] != 'RECOVERY_VALIDATED'
            or record['snapshot_manifest_digest'] != baseline['manifest_digest']
            or record['validated_views'] != recovery['validated_views'] or record['blocked_external_attempts'] != 0):
        raise ArtifactError('recovery durable record mismatch')
    if load_snapshot(restored, snapshot_id).ref.manifest_digest != baseline['manifest_digest']:
        raise ArtifactError('restored Snapshot differs from baseline')
    if set(recovery['validated_views']) != set(views['refs']):
        raise ArtifactError('recovery View coverage differs from baseline')
    for label, spec in views['refs'].items():
        if any(recovery['validated_views'][label].get(k) != v for k, v in spec.items()):
            raise ArtifactError('recovery View ref differs from baseline')
        if _view_loaders()[spec['kind']](restored, spec['view_id']).ref.manifest_digest != spec['manifest_digest']:
            raise ArtifactError('restored View differs from frozen reference')
    notebook = evidence['notebook']
    if notebook['snapshot_id'] != snapshot_id or notebook['view_refs'] != views['refs'] or notebook['target_digest'] != plan['target_digest']:
        raise ArtifactError('Notebook target binding mismatch')
    path = _safe_path(root, root / notebook['executed_notebook_path'])
    content = path.read_bytes(); cells = json.loads(content)['cells']
    code = [cell for cell in cells if cell['cell_type'] == 'code' and cell.get('source')]
    if (_digest(content) != notebook['content_digest'] or not code
            or any(cell.get('execution_count') is None or any(o.get('output_type') == 'error' for o in cell.get('outputs', [])) for cell in code)):
        raise ArtifactError('Notebook execution artifact incomplete')
    notebook_check = _entry(required['notebook']['validator'])(root, notebook, expected_views=views['refs'])
    if not isinstance(notebook_check, dict) or notebook_check.get('status') != 'NOTEBOOK_SMOKE_VALIDATED':
        raise ArtifactError('Notebook parameter/execution binding has not been validated')
    return {'status': 'EVIDENCE_VALIDATED', 'snapshot_id': snapshot_id,
            'gate_b_status': 'REVIEW_REQUIRED', 'ready_for_consumption': False}
