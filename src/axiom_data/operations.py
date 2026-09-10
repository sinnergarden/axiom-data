"""Public operational services. Run records describe execution, not Data identity."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from axiom_data.artifacts import (ArtifactError, _digest, _json_bytes, _identity,
    _layout, _safe_path, _ensure_directory, _write_file, _fsync_directory,
    load_raw_batch, load_snapshot)
from axiom_data.publication import writer


def _save(path, value):
    temporary = path.with_suffix('.pending')
    if temporary.exists():
        temporary.unlink()
    _write_file(temporary, _json_bytes(value))
    temporary.replace(path)
    _fsync_directory(path.parent)


def resolve_snapshot(data_root, snapshot_id):
    """Resolve current once at the human/service boundary, then verify a concrete ID."""
    layout = _layout(data_root)
    if snapshot_id == 'current':
        path = _safe_path(layout.root, layout.current_pointer)
        try:
            pointer = json.loads(path.read_bytes())
        except (FileNotFoundError, ValueError) as exc:
            raise ArtifactError('no valid current Snapshot pointer') from exc
        snapshot_id = pointer.get('snapshot_id')
    return load_snapshot(layout.root, snapshot_id).ref.snapshot_id


def inspect_snapshot(data_root, snapshot_id):
    """Read-only quality inventory; data extents never masquerade as admission."""
    from axiom_data.consumption import SnapshotReader
    concrete = resolve_snapshot(data_root, snapshot_id)
    reader = SnapshotReader(data_root, concrete)
    domains = {}
    for domain, commit in reader.commits.items():
        rows = reader.facts(domain) if domain not in {'trading_calendar', 'security_master'} else (
            reader.trading_calendar() if domain == 'trading_calendar' else reader.security_master())
        dates = sorted({str(r.get('session') or r.get('report_period') or r.get('effective_from'))
                        for r in rows if r.get('session') or r.get('report_period') or r.get('effective_from')})
        qualifications = Counter(r.get('pit_qualification', 'unknown') for r in rows)
        source_qualification=[]
        for ref in commit.manifest['ordered_raw_batch_refs']:
            if ref.get('source_profile_version')=='tushare_pr7_holder.v3':
                raw=load_raw_batch(data_root,ref['raw_batch_id'])
                exclusions=raw.manifest['summary'].get('source_qualification',[])
                if exclusions:source_qualification.append({'raw_batch_id':ref['raw_batch_id'],'records':exclusions})
        domains[domain] = {'commit_id': commit.ref.commit_id, 'contract': commit.ref.contract_version,
            'builder': commit.manifest['builder_implementation_ref'], 'rows': len(rows),
            'data_extent': {'first': dates[0] if dates else None, 'last': dates[-1] if dates else None},
            'pit_qualification_counts': dict(qualifications),
            'structural_validation': commit.manifest['validation_summary'],
            'full_scope_admission': 'NOT_ASSESSED',
            'source_record_qualification':source_qualification,
            'partitions': len(commit.manifest.get('partitions', commit.manifest['output_files']))}
    return {'snapshot_id': concrete, 'domains': domains,
            'note': 'Extents are not completeness; structural validation is not full-scope certification.'}


def _request(spec):
    """Only source parameters can enter durable metadata; credentials never can."""
    allowed = {'collector', 'domain', 'endpoint', 'params', 'economic_scope', 'availability_policy'}
    if not isinstance(spec, dict) or set(spec) != allowed:
        raise ArtifactError('source request requires the complete public plan schema')
    params = spec['params']
    keys = {'ts_code', 'exchange', 'list_status', 'start_date', 'end_date', 'trade_date',
            'index_code', 'period', 'report_type', 'src', 'level', 'l1_code', 'l2_code', 'l3_code', 'is_new', 'limit', 'offset'}
    if not isinstance(params, dict) or set(params) - keys:
        raise ArtifactError('unsupported source request parameter')
    if any(not isinstance(v, str) or len(v) > 128 for v in params.values()):
        raise ArtifactError('source parameters must be bounded strings')
    for k, v in params.items():
        if k in {'start_date', 'end_date', 'trade_date', 'period'}:
            from axiom_data.pr6_source import source_date
            source_date(v)
        elif k in {'ts_code', 'index_code'}:
            from axiom_data.domains.market import _symbol
            _symbol(v)
        elif k == 'exchange' and v not in {'', 'SSE', 'SZSE'}:
            raise ArtifactError('unsupported exchange')
        elif k == 'list_status' and v not in {'L', 'D', 'P'}:
            raise ArtifactError('unsupported listing status')
        elif k == 'report_type' and (not v.isdigit() or len(v) > 2):
            raise ArtifactError('invalid report type')
    from axiom_data.tushare import load_tushare_source_profile
    from axiom_data.dm1_source import load_dm1_source_profile
    from axiom_data.pr6_source import load_pr6_source_profile, validate_payload as pr6_validate
    from axiom_data.pr7_source import load_pr7_source_profile, validate_payload as pr7_validate
    from axiom_data.sw_source import load_profile as sw_profile, validate_request as sw_validate
    profiles = {'market': load_tushare_source_profile(), 'dm1': load_dm1_source_profile(),
                'pr6': load_pr6_source_profile(), 'pr6_bulk': load_pr6_source_profile('tushare_pr6.v2'), 'pr7': load_pr7_source_profile(), 'sw_pilot': sw_profile()}
    profiles['industry_qualification'] = sw_profile('tushare_industry_qualification.v1')
    profiles['pr7_holder'] = load_pr7_source_profile('tushare_pr7_holder.v2')
    profiles['pr7_holder_v3'] = load_pr7_source_profile('tushare_pr7_holder.v3')
    profiles['pr6_indicator'] = load_pr6_source_profile('tushare_fina_indicator.v1')
    family = spec['collector']
    if family not in profiles or spec['endpoint'] not in profiles[family]['endpoints']:
        raise ArtifactError('unsupported source operation')
    definition = profiles[family]['endpoints'][spec['endpoint']]
    if family == 'market':
        from axiom_data.tushare import _endpoint_domain
        permitted = {_endpoint_domain(spec['endpoint'])}
    elif family == 'dm1':
        permitted = set(definition['canonical_domains'])
    else:
        permitted = {definition['domain']}
    if spec['domain'] not in permitted:
        raise ArtifactError('source operation domain mismatch')
    if family in {'pr6','pr6_bulk','pr6_indicator'}:
        pr6_validate(spec['endpoint'], params, [], profile_version=profiles[family]['profile_version'])
    if family in {'pr7','pr7_holder','pr7_holder_v3'}:
        pr7_validate(spec['endpoint'], params, [],profile_version=profiles[family]['profile_version'])
    if family in {'sw_pilot','industry_qualification'}:
        sw_validate(spec['endpoint'], params, profile_version=profiles[family]['profile_version'])
    elif set(params) & {'src','level','l1_code','l2_code','l3_code','is_new','limit','offset'}:
        raise ArtifactError('SW parameters require the SW qualification collector')
    # These descriptive fields are enums/date maps, not arbitrary caller strings.
    if spec['availability_policy'] not in {'session_close', 'next_session_publication', 'revision_scan', 'reference_observation'}:
        raise ArtifactError('unsupported availability policy')
    economic = spec['economic_scope']
    if not isinstance(economic, dict) or set(economic) != {'start', 'end'}:
        raise ArtifactError('economic scope must be explicit')
    from axiom_data.pr6_source import source_date
    start, end = source_date(economic['start']), source_date(economic['end'])
    if start > end:
        raise ArtifactError('reversed economic scope')
    return _digest(_json_bytes({'spec': spec, 'binding': _source_binding(spec)}))


def _source_binding(spec):
    family, endpoint = spec['collector'], spec['endpoint']
    if family == 'market':
        from axiom_data.tushare import load_tushare_source_profile, tushare_source_profile_digest
        profile = load_tushare_source_profile()
        digest = tushare_source_profile_digest(profile)
        ref = profile['endpoints'][endpoint]['source_profile_ref']
    elif family == 'dm1':
        from axiom_data.dm1_source import load_dm1_source_profile, dm1_source_profile_digest
        profile = load_dm1_source_profile()
        digest = dm1_source_profile_digest(profile)
        ref = profile['endpoints'][endpoint]['source_profile_ref']
    elif family in {'pr6', 'pr6_bulk', 'pr6_indicator'}:
        from axiom_data.pr6_source import load_pr6_source_profile, profile_digest
        version = {'pr6_bulk':'tushare_pr6.v2','pr6_indicator':'tushare_fina_indicator.v1'}.get(family,'tushare_pr6.v1')
        profile = load_pr6_source_profile(version)
        digest = profile_digest(version)
        ref = 'tushare.pr6.' + endpoint
    elif family in {'sw_pilot','industry_qualification'}:
        from axiom_data.sw_source import load_profile, profile_digest
        version = 'tushare_sw_pilot.v1' if family=='sw_pilot' else 'tushare_industry_qualification.v1'
        profile = load_profile(version)
        digest = profile_digest(version)
        ref = 'tushare.sw-pilot.' + endpoint
    else:
        from axiom_data.pr7_source import load_pr7_source_profile, profile_digest
        version={'pr7_holder':'tushare_pr7_holder.v2','pr7_holder_v3':'tushare_pr7_holder.v3'}.get(family,'tushare_pr7.v1')
        profile = load_pr7_source_profile(version)
        digest = profile_digest(version)
        ref = 'tushare.pr7.' + endpoint
    return {'source_profile_ref': ref, 'source_profile_version': profile['profile_version'],
            'source_profile_digest': digest, 'fields': profile['endpoints'][endpoint]['fields']}


def _check_collected(raw, spec):
    binding = _source_binding(spec)
    manifest = raw.manifest
    if (manifest['schema_version'] != 'raw_batch.v2' or manifest['status'] != 'success'
        or manifest['domain'] != spec['domain']
        or any(manifest.get(k) != v for k, v in binding.items() if k != 'fields')
        or manifest['request'] != {'endpoint': spec['endpoint'], 'params': spec['params'],
                                   'fields': binding['fields']}):
        raise ArtifactError('resume RawBatch/source request binding mismatch')


def collect_requests(data_root, *, run_id, requests, client=None):
    """Resume only exact requests and revalidated immutable refs; partial is never COMPLETE.

    This collection stage alone cannot accept a baseline or move a pointer.
    """
    from axiom_data.tushare import TushareCollector
    from axiom_data.dm1_source import TushareDm1Collector
    from axiom_data.pr6_source import Pr6Collector
    from axiom_data.pr7_source import Pr7Collector
    from axiom_data.sw_source import SwQualificationCollector, IndustryQualificationCollector
    _identity('run_id', run_id)
    if not isinstance(requests, list) or not requests:
        raise ArtifactError('explicit nonempty source request plan required')
    keys = [_request(spec) for spec in requests]
    if len(set(keys)) != len(keys):
        raise ArtifactError('duplicate requests in plan')
    plan_digest = _digest(_json_bytes({'requests': requests, 'request_keys': keys}))
    layout = _layout(data_root)
    with writer(layout.root):
        directory = layout.root/'operations'/run_id
        _ensure_directory(layout.root, directory)
        path = _safe_path(layout.root, directory/'collection.json')
        state = json.loads(path.read_bytes()) if path.exists() else {
            'schema_version': 'collection_run.v1', 'run_id': run_id,
            'plan_digest': plan_digest, 'requests': requests, 'completed': {}, 'failed': {},
            'stage': 'COLLECTION', 'status': 'RUNNING'}
        if state['plan_digest'] != plan_digest or state['requests'] != requests:
            raise ArtifactError('resume plan differs from frozen run plan')
        _save(path, state)
        collectors = {'market': TushareCollector(layout.root, client),
                      'dm1': TushareDm1Collector(layout.root, client),
                      'pr6': Pr6Collector(layout.root, client), 'pr6_bulk': Pr6Collector(layout.root, client), 'pr7': Pr7Collector(layout.root, client),
                      'sw_pilot': SwQualificationCollector(layout.root, client)}
        collectors['industry_qualification'] = IndustryQualificationCollector(layout.root, client)
        collectors['pr7_holder'] = Pr7Collector(layout.root, client)
        collectors['pr7_holder_v3'] = Pr7Collector(layout.root, client)
        collectors['pr6_indicator'] = Pr6Collector(layout.root, client)
        consecutive_failures = 0
        for key, spec in zip(keys, requests):
            try:
                if key in state['completed']:
                    raw = load_raw_batch(layout.root, state['completed'][key])
                    _check_collected(raw, spec)
                    state['failed'].pop(key, None)
                    continue
                collector = collectors[spec['collector']]
                args = (spec['domain'], spec['endpoint'], spec['params']) if spec['collector'] == 'dm1' else (spec['endpoint'], spec['params'])
                versions={'pr6_bulk':'tushare_pr6.v2','pr6_indicator':'tushare_fina_indicator.v1','pr7_holder':'tushare_pr7_holder.v2','pr7_holder_v3':'tushare_pr7_holder.v3'}
                ref = collector.collect(*args, **({'profile_version':versions[spec['collector']]} if spec['collector'] in versions else {}))
                raw = load_raw_batch(layout.root, ref.raw_batch_id)
                _check_collected(raw, spec)
                state['completed'][key] = ref.raw_batch_id
                state['failed'].pop(key, None)
                consecutive_failures = 0
            except Exception as exc:
                # Supplier exception text may contain request credentials or transport URLs.
                state['failed'][key] = {'error_type': type(exc).__name__}
                consecutive_failures += 1
            state['updated_at'] = datetime.now(timezone.utc).isoformat()
            _save(path, state)
            if consecutive_failures >= 3:
                break
        state['pending_count'] = len(requests) - len(set(state['completed']) | set(state['failed']))
        state['status'] = 'COMPLETE' if len(state['completed']) == len(requests) and not state['failed'] else 'FAILED'
        _save(path, state)
        return state


def compare_pr7_projection(data_root, snapshot_id, view_id, *, symbols, fields, start_session, end_session):
    """Public acceptance probe using Reader PIT facts and Qlib's public row reader."""
    import math
    from axiom_data.consumption import SnapshotReader, QlibViewReader
    from axiom_data.pr7_views import load_pr7_fact_view, LEAF_DOMAINS
    from axiom_data.pit import instant
    reader = SnapshotReader(data_root, snapshot_id)
    view = load_pr7_fact_view(data_root, view_id)
    if view.manifest['snapshot_ref']['snapshot_id'] != snapshot_id:
        raise ArtifactError('View/Snapshot mismatch')
    scope = view.manifest['scope']
    if (not set(symbols) <= set(scope['symbols']) or not set(fields) <= set(LEAF_DOMAINS)
        or start_session < scope['start_session'] or end_session > scope['end_session']
        or start_session > end_session):
        raise ArtifactError('comparison outside View scope')
    actual = {(r['symbol'], r['session']): r for r in QlibViewReader(data_root, view_id).market_daily(include_missing=True)
              if r['symbol'] in symbols and start_session <= r['session'] <= end_session}
    expected_keys = {(r['symbol'], r['session']) for r in view.rows
                     if r['symbol'] in symbols and start_session <= r['session'] <= end_session}
    if set(actual) != expected_keys:
        raise ArtifactError('direct/Qlib session keys differ')
    for (symbol, session), values in actual.items():
        cutoff = min(instant(view.manifest['knowledge_cutoff']), instant(session+'T23:59:59+08:00')).isoformat()
        for field in fields:
            expected = reader.leaf_fact(field, symbol=symbol, target_session=session,
                knowledge_cutoff=cutoff, pit_policy=view.manifest['pit_policy'])['value']
            got = values[field]
            if ((expected is None) != (got is None) or expected is not None and
                not math.isclose(expected, got, rel_tol=1e-6, abs_tol=1e-6)):
                raise ArtifactError('direct/Qlib value or missingness differs')
    return {'status': 'PASS', 'snapshot_id': snapshot_id, 'view_id': view_id,
            'rows': len(actual), 'fields': fields, 'start_session': start_session, 'end_session': end_session}


def assemble_candidate(data_root, *, run_id, domain_inputs, parent_snapshot_id=None):
    """Build explicit changed domains and an immutable candidate; never promote it.

    A required-view or full-admission stage must accept the candidate before any
    default pointer can move. Frozen raw remains published after a failed build.
    """
    from axiom_data import BuildApplication, create_snapshot, SnapshotReader, validate_domain_commit_closure
    from axiom_data.artifacts import _DOMAIN_DEPENDENCIES
    from axiom_data.domains import PR7_SNAPSHOT_DOMAINS
    from axiom_data.tushare import TushareMarketBuilder
    from axiom_data.dm1_source import TushareDm1Builder
    from axiom_data.pr6_source import Pr6Builder
    from axiom_data.pr7_source import Pr7Builder
    from axiom_data.domains import PR6_DOMAINS, PR7_DOMAINS
    _identity('run_id', run_id)
    layout = _layout(data_root)
    if not isinstance(domain_inputs, dict) or not domain_inputs or set(domain_inputs)-set(PR7_SNAPSHOT_DOMAINS):
        raise ArtifactError('explicit registered domain input plan required')
    allowed_config = {'symbols', 'start_session', 'end_session', 'membership_end_exclusive', 'security_boundary_policy', 'industry_source_profile', 'session_suspension_policy', 'market_source_partitioning','dm1_source_partitioning'}
    for domain, spec in domain_inputs.items():
        if not isinstance(spec, dict) or set(spec) != {'raw_batch_ids', 'contract_version', 'config', 'new_lineage'}:
            raise ArtifactError('domain input requires raw refs, contract, config and lineage decision')
        if not isinstance(spec['config'], dict) or set(spec['config'])-allowed_config:
            raise ArtifactError('unsupported public builder config')
        if 'security_boundary_policy' in spec['config'] and (
            domain != 'security_master' or spec['config']['security_boundary_policy'] != 'exchange_security.v1'
        ):
            raise ArtifactError('unsupported security boundary policy')
        if 'industry_source_profile' in spec['config'] and (domain!='industry_membership' or spec['config']['industry_source_profile']!='tushare_sw2021.v1'):
            raise ArtifactError('unsupported industry source profile')
        if 'session_suspension_policy' in spec['config'] and (domain not in {'market_daily','security_status'} or spec['config']['session_suspension_policy'] not in {'session_suspension.v1','session_suspension.v2','session_suspension.v3'}):
            raise ArtifactError('unsupported session suspension policy')
        if 'market_source_partitioning' in spec['config'] and (domain!='market_daily' or spec['config']['market_source_partitioning']!='security.v1'):
            raise ArtifactError('unsupported market source partitioning')
        if 'dm1_source_partitioning' in spec['config']:
            from axiom_data.dm1_source import _EXPECTED_ENDPOINTS
            if domain not in _EXPECTED_ENDPOINTS or spec['config']['dm1_source_partitioning']!='security.v1':
                raise ArtifactError('unsupported D-M1 source partitioning')
        from axiom_data.build import BuildRequest
        BuildRequest(None, spec['raw_batch_ids'], [], spec['contract_version'])
        if type(spec['new_lineage']) is not bool:
            raise ArtifactError('new_lineage must be boolean')
    with writer(layout.root):
        parent_id = resolve_snapshot(layout.root, parent_snapshot_id) if parent_snapshot_id else None
        parent = SnapshotReader(layout.root, parent_id) if parent_id else None
        commits = {d: c.ref.commit_id for d, c in parent.commits.items()} if parent else {}
        if set(commits) | set(domain_inputs) != set(PR7_SNAPSHOT_DOMAINS):
            raise ArtifactError('V1 candidate requires the complete registered domain set')
        plan = {'parent_snapshot_id': parent_id, 'domains': domain_inputs}
        directory = layout.root/'operations'/run_id
        _ensure_directory(layout.root, directory)
        path = _safe_path(layout.root, directory/'build.json')
        state = json.loads(path.read_bytes()) if path.exists() else {
            'schema_version':'candidate_build_run.v1','run_id':run_id,'plan':plan,
            'plan_digest':_digest(_json_bytes(plan)), 'stage':'CANONICAL_BUILD',
            'status':'RUNNING', 'published_commits':{}, 'failed':{}}
        if state['plan_digest'] != _digest(_json_bytes(plan)) or state['plan'] != plan:
            raise ArtifactError('resume build plan mismatch')
        # A mutable execution record cannot keep a stale successful candidate on failure.
        state.pop('snapshot_id', None)
        state.pop('domain_commit_ids', None)
        state['ready_for_consumption'] = False
        state.update(status='RUNNING', stage='CANONICAL_BUILD')
        _save(path,state)
        for domain in PR7_SNAPSHOT_DOMAINS:
            if domain not in domain_inputs:
                continue
            spec = domain_inputs[domain]
            try:
                config = dict(spec['config'], storage_policy='domain_time_blocks.v1',
                              no_change_policy='reuse_equal_state.v1')
                deps = {name: commits[name] for name in _DOMAIN_DEPENDENCIES[domain]}
                if domain in {'trading_calendar','security_master','market_daily'}:
                    options = {'calendar_commit_id':deps['trading_calendar'],
                               'security_master_commit_id':deps['security_master']} if domain=='market_daily' else {}
                    if domain == 'security_master' and config.get('security_boundary_policy') == 'exchange_security.v1':
                        from axiom_data.exchange_security import ExchangeSecurityBuilder
                        builder = ExchangeSecurityBuilder(layout.root,builder_config=config)
                    else:
                        builder = TushareMarketBuilder(layout.root,domain,builder_config=config,**options)
                else:
                    cls = Pr6Builder if domain in PR6_DOMAINS else Pr7Builder if domain in PR7_DOMAINS else TushareDm1Builder
                    builder = cls(layout.root,domain,builder_config=config,dependency_commit_ids=deps)
                previous = None if spec['new_lineage'] else commits.get(domain)
                if domain in state['published_commits']:
                    from axiom_data.artifacts import _builder_implementation_ref, _raw_ref, _commit_ref
                    identity = state['published_commits'][domain]
                    resumed = validate_domain_commit_closure(layout.root,domain,identity)
                    if identity == previous:
                        # No-change reuse has no new input refs in the parent manifest;
                        # replay the explicit request to prove it still yields that state.
                        replay = BuildApplication(domain,builder).build(previous,spec['raw_batch_ids'],[],spec['contract_version'])
                        if replay.commit_id != identity:
                            raise ArtifactError('resume no-change result differs from request')
                    else:
                        parent_ref = _commit_ref(validate_domain_commit_closure(layout.root,domain,previous)) if previous else None
                        expected = {
                            'contract_version': spec['contract_version'],
                            'parent_commit_ref': parent_ref,
                            'ordered_raw_batch_refs': [_raw_ref(load_raw_batch(layout.root,r)) for r in spec['raw_batch_ids']],
                            'ordered_patch_refs': [],
                            'builder_config': builder.builder_config,
                            'builder_implementation_ref': _builder_implementation_ref(builder),
                            'dependency_commit_refs': {d:_commit_ref(validate_domain_commit_closure(layout.root,d,i)) for d,i in deps.items()},
                        }
                        if any(resumed.manifest.get(k) != v for k,v in expected.items()):
                            raise ArtifactError('resume commit does not match explicit build request')
                    commits[domain] = identity
                    state['failed'].pop(domain,None)
                    continue
                ref = BuildApplication(domain,builder).build(previous,spec['raw_batch_ids'],[],spec['contract_version'])
                commits[domain] = ref.commit_id
                state['published_commits'][domain] = ref.commit_id
                state['failed'].pop(domain,None)
                _save(path,state)
            except Exception as exc:
                state['failed'][domain] = {'error_type':type(exc).__name__}
                state['status'] = 'FAILED'
                _save(path,state)
                return state
        try:
            candidate = create_snapshot(layout.root,commits)
            verified = SnapshotReader(layout.root,candidate.snapshot_id)
            if {d:c.ref.commit_id for d,c in verified.commits.items()} != commits:
                raise ArtifactError('candidate domain closure mismatch')
            state.update(snapshot_id=candidate.snapshot_id, domain_commit_ids=commits,
                         status='CANDIDATE_BUILT', stage='REQUIRED_VIEWS_AND_FULL_ADMISSION',
                         ready_for_consumption=False, failed={})
        except Exception as exc:
            state.update(status='FAILED', failed={'snapshot':{'error_type':type(exc).__name__}})
        _save(path,state)
        return state
