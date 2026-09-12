"""Public operational services. Run records describe execution, not Data identity."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from axiom_data.artifacts import (ArtifactError, _digest, _json_bytes, _identity,
    _layout, _safe_path, _ensure_directory, _write_file, _fsync_directory,
    load_raw_batch, load_snapshot)
from axiom_data.publication import writer
from axiom_data.verification_cache import candidate_verification


def _save(path, value):
    temporary = path.with_suffix('.pending')
    if temporary.exists():
        temporary.unlink()
    _write_file(temporary, _json_bytes(value))
    temporary.replace(path)
    _fsync_directory(path.parent)


def _resolve_snapshot_id(data_root, snapshot_id):
    """Resolve the pointer once; the receiving Reader validates the closure."""
    layout = _layout(data_root)
    if snapshot_id == 'current':
        path = _safe_path(layout.root, layout.current_pointer)
        try:
            pointer = json.loads(path.read_bytes())
        except (FileNotFoundError, ValueError) as exc:
            raise ArtifactError('no valid current Snapshot pointer') from exc
        if not isinstance(pointer, dict):
            raise ArtifactError('no valid current Snapshot pointer')
        snapshot_id = pointer.get('snapshot_id')
    return _identity('snapshot_id', snapshot_id)


def resolve_snapshot(data_root, snapshot_id):
    """Resolve current once at the human/service boundary and verify its closure."""
    return load_snapshot(data_root, _resolve_snapshot_id(data_root, snapshot_id)).ref.snapshot_id


def inspect_scope(data_root, snapshot_id, *, domain, symbols, start_session, end_session, fields):
    """Read-only canonical session audit on one concretely resolved Snapshot."""
    from axiom_data.consumption import SnapshotReader
    from axiom_data.scope_coverage import session_coverage
    reader=SnapshotReader(data_root,_resolve_snapshot_id(data_root,snapshot_id))
    return session_coverage(reader,domain,symbols=symbols,start_session=start_session,
                            end_session=end_session,fields=fields)


def inspect_snapshot(data_root, snapshot_id):
    """Read-only quality inventory; data extents never masquerade as admission."""
    from axiom_data.consumption import SnapshotReader
    concrete = _resolve_snapshot_id(data_root, snapshot_id)
    reader = SnapshotReader(data_root, concrete)
    domains = {}
    for domain, commit in reader.commits.items():
        rows = commit.rows
        first = last = None
        qualifications = Counter()
        for row in rows:
            value = row.get('session') or row.get('report_period') or row.get('effective_from') or row.get('effective_date')
            if value:
                value = str(value)
                first = value if first is None else min(first, value)
                last = value if last is None else max(last, value)
            qualifications[row.get('pit_qualification', 'unknown')] += 1
        source_qualification=[]
        for ref in commit.manifest['ordered_raw_batch_refs']:
            if ref.get('source_profile_version')=='tushare_pr7_holder.v3':
                raw=load_raw_batch(data_root,ref['raw_batch_id'])
                exclusions=raw.manifest['summary'].get('source_qualification',[])
                if exclusions:source_qualification.append({'raw_batch_id':ref['raw_batch_id'],'records':exclusions})
        domains[domain] = {'commit_id': commit.ref.commit_id, 'contract': commit.ref.contract_version,
            'builder': commit.manifest['builder_implementation_ref'], 'rows': len(rows),
            'data_extent': {'first': first, 'last': last},
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


def plan_daily(data_root, snapshot_id, *, source_requests):
    """Inspect explicit per-source daily requests without collection or publication.

    Requests may cover different economic dates and revision windows. Availability
    policies describe when collection is expected; only actual collection can
    establish whether a required supplier response exists.
    """
    from axiom_data.consumption import SnapshotReader
    if not isinstance(source_requests, list):
        raise ArtifactError('daily source requests must be an explicit list')
    keys = [_request(spec) for spec in source_requests]
    if len(set(keys)) != len(keys):
        raise ArtifactError('duplicate daily source request')
    policies = {'margin_daily': 'next_session_publication',
        **{d: 'revision_scan' for d in ('financial_events', 'holder_count_events',
           'top_holders_reports', 'forecast_observations', 'corporate_actions')},
        **{d: 'reference_observation' for d in ('trading_calendar', 'security_master',
           'universe_membership', 'industry_membership')}}
    for spec in source_requests:
        if spec['availability_policy'] != policies.get(spec['domain'], 'session_close'):
            raise ArtifactError('daily availability policy does not match domain')
    concrete = _resolve_snapshot_id(data_root, snapshot_id)
    reader = SnapshotReader(data_root, concrete)
    changed = sorted({spec['domain'] for spec in source_requests})
    if set(changed) - set(reader.commits):
        raise ArtifactError('daily domain is absent from the explicit parent Snapshot')
    affected = set(changed)
    while True:
        expanded = affected | {d for d, c in reader.commits.items()
            if set(c.manifest['dependency_commit_refs']) & affected}
        if expanded == affected:
            break
        affected = expanded
    requests = [{'request_id': key, **json.loads(_json_bytes(spec)),
                 'source_binding': _source_binding(spec),
                 'required': True, 'availability_state': 'UNCONFIRMED_UNTIL_COLLECTION',
                 'expected_t_plus_one': spec['availability_policy'] == 'next_session_publication'}
                for key, spec in zip(keys, source_requests)]
    plan = {'schema_version': 'daily_plan.v1', 'parent_snapshot_id': concrete,
        'parent_commits': {d: c.ref.commit_id for d, c in reader.commits.items()},
        'source_requests': requests, 'source_change_domains': changed,
        'changed_domain_candidates': sorted(affected),
        'dependency_review_domains': sorted(affected - set(changed)),
        'unchanged_domain_candidates': sorted(set(reader.commits) - affected),
        'unconfirmed_required_requests': keys,
        'ready_for_consumption': False}
    return dict(plan, plan_digest=_digest(_json_bytes(plan)))


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
    from axiom_data.source_completeness import validate_raw_completeness
    validate_raw_completeness(raw)



def _collection_failure(exc, raw_id=None):
    failure = {'error_type': type(exc).__name__}
    if raw_id is not None:
        failure['raw_batch_id'] = _identity('raw_batch_id', raw_id)
    return failure


def _checked_collection_record(root, spec, raw_id):
    """Current Raw validation, never a historical completed flag, is authority."""
    from axiom_data.source_completeness import SourceCompletenessError
    raw = None
    try:
        raw = load_raw_batch(root, raw_id)
        _check_collected(raw, spec)
    except Exception as exc:
        record = {'state': 'FAILED_TERMINAL', 'raw_batch_id': raw_id,
                  'failure': _collection_failure(exc, raw_id), 'split': None}
        if isinstance(exc, SourceCompletenessError) and raw is not None:
            from axiom_data.bootstrap_sources import plan_truncated_raw_split
            try:
                split = plan_truncated_raw_split(raw)
            except ArtifactError:
                # Re-fetch an invalid response; a saturated indivisible window
                # has no supported recovery and must stay terminal.
                from axiom_data.source_completeness import completeness_policy
                policy = completeness_policy(raw.manifest['source_profile_version'],
                                             raw.manifest['request'].get('endpoint'))
                saturated = (policy['status'] == 'established' and
                             len(json.loads(raw.payload)) >= policy['limit'])
                record['state'] = 'FAILED_TERMINAL' if saturated else 'NEEDS_RETRY'
            else:
                record.update(state='NEEDS_SPLIT', split=split)
        return record
    return {'state': 'VALID_COMPLETE', 'raw_batch_id': raw_id, 'failure': None, 'split': None}


def _collection_checkpoint(plan_digest, key, record):
    return {'schema_version': 'collection_checkpoint.v2', 'plan_digest': plan_digest,
            'request_id': key, **record}


def _record_collection_state(state, key, record):
    state['request_states'][key] = record
    state['completed'].pop(key, None)
    state['failed'].pop(key, None)
    if record['state'] == 'VALID_COMPLETE':
        state['completed'][key] = record['raw_batch_id']
    elif record['failure'] is not None:
        state['failed'][key] = record['failure']


def _supersede_collection_request(root, *, run_id, key, split, child_run_id):
    """Freeze the child graph before executing it; retain the rejected parent."""
    with writer(root):
        directory = _safe_path(root, root/'operations'/run_id)
        path = _safe_path(root, directory/'collection.json')
        state = json.loads(path.read_bytes())
        record = state['request_states'][key]
        if record['state'] not in {'NEEDS_SPLIT', 'SUPERSEDED_BY_SPLIT'} or record['split'] != split:
            raise ArtifactError('collection split differs from revalidated parent')
        child_refs = {'run_id': child_run_id, 'request_ids': [_request(s) for s in split['requests']]}
        if record.get('child_refs', child_refs) != child_refs:
            raise ArtifactError('collection split child graph changed')
        record = dict(record, state='SUPERSEDED_BY_SPLIT', failure=None, child_refs=child_refs)
        checkpoint = directory/'collection-checkpoints'/(key.removeprefix('sha256:')+'.json')
        _save(_safe_path(root, checkpoint), _collection_checkpoint(state['plan_digest'], key, record))
        _record_collection_state(state, key, record)
        # The parent page is superseded, never an admitted canonical input.
        state['status'] = 'RECOVERY_REQUIRED'
        _save(path, state)


def collect_requests(data_root, *, run_id, requests, client=None, observed_raw_batch_ids=None):
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
    requests=json.loads(_json_bytes(requests))
    keys = [_request(spec) for spec in requests]
    if len(set(keys)) != len(keys):
        raise ArtifactError('duplicate requests in plan')
    bound={}
    layout = _layout(data_root)
    resuming = _safe_path(layout.root, layout.root/'operations'/run_id/'collection.json').exists()
    projection={'requests':requests,'request_keys':keys}
    if observed_raw_batch_ids is not None:
        from axiom_data.build import _validate_identity
        if not isinstance(observed_raw_batch_ids,dict) or not set(observed_raw_batch_ids)<=set(keys):
            raise ArtifactError('observed Raw refs must map planned request IDs')
        bound=json.loads(_json_bytes(observed_raw_batch_ids));specs=dict(zip(keys,requests))
        for key,identity in bound.items():
            _validate_identity('raw_batch_id',identity)
            if not resuming:
                _check_collected(load_raw_batch(data_root,identity),specs[key])
        projection['observed_raw_batch_ids']=bound
    plan_digest = _digest(_json_bytes(projection))
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
        checkpoints = _safe_path(layout.root, directory/'collection-checkpoints')
        _ensure_directory(layout.root, checkpoints)
        # Per-request checkpoints remain the durable authority between summary
        # writes. Upgrade legacy refs by validating them before any source call.
        saved = {}
        records = {}
        for key in keys:
            checkpoint = _safe_path(layout.root, checkpoints/(key.removeprefix('sha256:')+'.json'))
            result = json.loads(checkpoint.read_bytes()) if checkpoint.exists() else None
            if result is not None:
                if result.get('plan_digest') != plan_digest or result.get('request_id') != key:
                    raise ArtifactError('collection checkpoint binding mismatch')
                if result.get('schema_version') == 'collection_checkpoint.v1':
                    if set(result) != {'schema_version','plan_digest','request_id','raw_batch_id','failure'}:
                        raise ArtifactError('invalid legacy collection checkpoint')
                    failure = result['failure']
                    if failure is not None and (not isinstance(failure, dict) or
                            set(failure) not in ({'error_type'}, {'error_type','raw_batch_id'}) or
                            not isinstance(failure['error_type'], str)):
                        raise ArtifactError('invalid collection checkpoint failure')
                    failed_id = (failure or {}).get('raw_batch_id')
                    identity = result['raw_batch_id'] or failed_id
                    if identity is None and failure is None:
                        raise ArtifactError('collection checkpoint has no result')
                    if identity is not None and failed_id is not None and identity != failed_id:
                        raise ArtifactError('ambiguous legacy Raw checkpoint refs')
                    records[key] = {'state':'NEEDS_RETRY', 'raw_batch_id':identity,
                                    'failure':failure, 'split':None}
                elif result.get('schema_version') == 'collection_checkpoint.v2':
                    record = {k:v for k,v in result.items() if k not in {'schema_version','plan_digest','request_id'}}
                    if (set(record) not in ({'state','raw_batch_id','failure','split'},
                                           {'state','raw_batch_id','failure','split','child_refs'}) or
                            record['state'] not in {'VALID_COMPLETE','NEEDS_RETRY','NEEDS_SPLIT',
                                                    'FAILED_TERMINAL','SUPERSEDED_BY_SPLIT'}):
                        raise ArtifactError('invalid collection checkpoint state')
                    records[key] = record
                else:
                    raise ArtifactError('unsupported collection checkpoint schema')
                saved[key] = result
            else:
                failure = state['failed'].get(key)
                identity = state['completed'].get(key) or (failure or {}).get('raw_batch_id')
                records[key] = state.get('request_states', {}).get(key, {
                    'state':'PENDING','raw_batch_id':identity,'failure':failure,'split':None})
            if key in bound:
                if ((key in state['completed'] and state['completed'][key] != bound[key]) or
                        records[key]['raw_batch_id'] not in (None, bound[key])):
                    state.update(status='FAILED',failed={key:{'error_type':'ArtifactError'}})
                    state['completed'].pop(key, None)
                    _save(path,state)
                    raise ArtifactError('completed Raw ref differs from explicitly bound observation')
                records[key]['raw_batch_id'] = bound[key]
        state.update(status='RUNNING',stage='COLLECTION',completed={},failed={},request_states={})
        if observed_raw_batch_ids is not None:
            state['observed_raw_batch_ids'] = bound
        for key, spec in zip(keys, requests):
            previous = records[key]
            identity = previous['raw_batch_id']
            if identity is not None:
                record = _checked_collection_record(layout.root, spec, identity)
                if previous['state'] == 'SUPERSEDED_BY_SPLIT':
                    if record['state'] != 'NEEDS_SPLIT' or record['split'] != previous['split']:
                        raise ArtifactError('superseded parent no longer matches frozen split')
                    record = dict(record, state='SUPERSEDED_BY_SPLIT', failure=None,
                                  child_refs=previous['child_refs'])
            else:
                record = {'state':'NEEDS_RETRY' if previous['failure'] else 'PENDING',
                          'raw_batch_id':None,'failure':previous['failure'],'split':None}
            _record_collection_state(state, key, record)
            upgraded = _collection_checkpoint(plan_digest, key, record)
            if identity is not None and saved.get(key) != upgraded:
                checkpoint = _safe_path(layout.root, checkpoints/(key.removeprefix('sha256:')+'.json'))
                _save(checkpoint, upgraded)
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
            record = state['request_states'][key]
            if record['state'] not in {'PENDING','NEEDS_RETRY'} or key in bound:
                continue
            try:
                collector = collectors[spec['collector']]
                args = (spec['domain'], spec['endpoint'], spec['params']) if spec['collector'] == 'dm1' else (spec['endpoint'], spec['params'])
                versions={'pr6_bulk':'tushare_pr6.v2','pr6_indicator':'tushare_fina_indicator.v1','pr7_holder':'tushare_pr7_holder.v2','pr7_holder_v3':'tushare_pr7_holder.v3'}
                ref = collector.collect(*args, **({'profile_version':versions[spec['collector']]} if spec['collector'] in versions else {}))
                record = _checked_collection_record(layout.root, spec, ref.raw_batch_id)
            except Exception as exc:
                # Never persist supplier exception text or credential-bearing URLs.
                rejected_raw = getattr(exc, 'raw_batch_id', None)
                record = (_checked_collection_record(layout.root, spec, rejected_raw) if rejected_raw else
                          {'state':'NEEDS_RETRY','raw_batch_id':None,'failure':_collection_failure(exc),'split':None})
            _record_collection_state(state, key, record)
            state['updated_at'] = datetime.now(timezone.utc).isoformat()
            checkpoint = _safe_path(layout.root, checkpoints/(key.removeprefix('sha256:')+'.json'))
            _save(checkpoint, _collection_checkpoint(plan_digest, key, record))
            # A supported split is a planned recovery, not a transport failure.
            consecutive_failures = (0 if record['state'] in {'VALID_COMPLETE','NEEDS_SPLIT'}
                                    else consecutive_failures + 1)
            if consecutive_failures >= 3:
                break
        state['pending_count'] = sum(r['state'] == 'PENDING' for r in state['request_states'].values())
        state['status'] = 'COMPLETE' if len(state['completed']) == len(requests) else 'FAILED'
        _save(path, state)
        return state


def compare_pr7_projection(data_root, snapshot_id, view_id, *, symbols, fields, start_session, end_session):
    """Public acceptance probe using Reader PIT facts and Qlib's public row reader."""
    import math
    from axiom_data.consumption import SnapshotReader, QlibViewReader
    from axiom_data.pr7_views import _load_pr7_fact_view, LEAF_DOMAINS
    from axiom_data.pit import instant
    reader = SnapshotReader(data_root, snapshot_id)
    view = _load_pr7_fact_view(data_root, view_id, checked_reader=reader)
    if view.manifest['snapshot_ref']['snapshot_id'] != snapshot_id:
        raise ArtifactError('View/Snapshot mismatch')
    scope = view.manifest['scope']
    if (not set(symbols) <= set(scope['symbols']) or not set(fields) <= set(LEAF_DOMAINS)
        or start_session < scope['start_session'] or end_session > scope['end_session']
        or start_session > end_session):
        raise ArtifactError('comparison outside View scope')
    # The View files and full Snapshot were checked above in this call. Decode
    # through the existing Qlib reader without another identical closure load.
    binary = object.__new__(QlibViewReader)
    binary.data_root = Path(data_root)
    binary.view = view
    binary.path = _layout(data_root).derived_commits('pr7_fact') / view_id
    actual = {(r['symbol'], r['session']): r for r in binary.market_daily(include_missing=True)
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


def _validate_domain_inputs(domain_inputs, *, pending_domains=()):
    from axiom_data.domains import PR7_SNAPSHOT_DOMAINS
    if not isinstance(domain_inputs, dict) or not domain_inputs or set(domain_inputs)-set(PR7_SNAPSHOT_DOMAINS):
        raise ArtifactError('explicit registered domain input plan required')
    allowed_config = {'symbols', 'start_session', 'end_session', 'membership_end_exclusive', 'security_boundary_policy', 'security_session_scope', 'industry_source_profile', 'session_suspension_policy', 'market_source_partitioning','dm1_source_partitioning','universe_acquisition','top10_qualification','margin_qualification','corporate_action_observations','capital_qualification','limit_qualification','forecast_source_types','corporate_action_reobservation'}
    for domain, spec in domain_inputs.items():
        if not isinstance(spec, dict) or set(spec) != {'raw_batch_ids', 'contract_version', 'config', 'new_lineage'}:
            raise ArtifactError('domain input requires raw refs, contract, config and lineage decision')
        if not isinstance(spec['config'], dict) or set(spec['config'])-allowed_config:
            raise ArtifactError('unsupported public builder config')
        config = spec['config']
        from axiom_data.consumption import _symbols, _session
        from axiom_data.contracts import load_contract
        if load_contract(spec['contract_version'])['domain'] != domain:
            raise ArtifactError('public domain/contract mismatch')
        if 'symbols' in config:
            _symbols(config['symbols'])
        for key in ('start_session', 'end_session'):
            if key in config and _session(config[key], key) != config[key]:
                raise ArtifactError('public scope requires canonical ISO sessions')
        if 'start_session' in config and 'end_session' in config and config['start_session'] > config['end_session']:
            raise ArtifactError('reversed public scope')
        if 'security_session_scope' in config:
            from axiom_data.dm1_source import _SECURITY_SESSION_SCOPE_DOMAINS
            if (domain not in _SECURITY_SESSION_SCOPE_DOMAINS or config['security_session_scope'] != 'exchange_security.v1'
                    or not {'symbols','start_session','end_session'} <= set(config)):
                raise ArtifactError('unsupported or incomplete public security session scope')
        for key, expected in {'capital_qualification': ('security_capital', 'capital_conflict.v1'),
                              'corporate_action_reobservation': ('corporate_actions', 'corporate_action_reobservation.v1'),
                              'limit_qualification': ('price_limits', 'zero_limit_pair.v1'),
                              'forecast_source_types': ('forecast_observations', 'forecast_source_types.v1')}.items():
            if key in spec['config'] and (domain, spec['config'][key]) != expected:
                raise ArtifactError('unsupported source qualification: '+key)
        if 'universe_acquisition' in spec['config'] and domain!='universe_membership':
            raise ArtifactError('universe acquisition requires universe_membership')
        if 'top10_qualification' in spec['config'] and (domain!='top_holders_reports' or spec['config']['top10_qualification']!='top10_ambiguity.v1'):
            raise ArtifactError('unsupported Top10 qualification')
        if 'margin_qualification' in spec['config'] and (domain!='margin_daily' or spec['config']['margin_qualification']!='margin_negative_repayment.v1'):
            raise ArtifactError('unsupported margin qualification')
        if 'corporate_action_observations' in spec['config'] and (domain!='corporate_actions' or spec['config']['corporate_action_observations']!='corporate_action_observations.v1'):
            raise ArtifactError('unsupported corporate action observations')
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
        if domain in pending_domains and spec['raw_batch_ids'] == []:
            from axiom_data.build import _registered_contract
            _registered_contract('contract_version', spec['contract_version'])
        else:
            BuildRequest(None, spec['raw_batch_ids'], [], spec['contract_version'])
        if type(spec['new_lineage']) is not bool:
            raise ArtifactError('new_lineage must be boolean')


def daily(data_root, *, run_id, snapshot_id, source_requests, domain_inputs, client=None,
          observed_raw_batch_ids=None):
    """Execute the frozen source plan and build its immutable daily candidate.

    Requests retain independent economic windows. Each domain input lists any
    existing frozen Raw needed in addition to collected inputs. Candidate success
    still requires View/admission acceptance before consumption or promotion.
    """
    import fcntl
    from axiom_data.build import _validate_identity
    _identity('run_id',run_id)
    concrete=_validate_identity('snapshot_id',snapshot_id)
    if not isinstance(source_requests,list) or not source_requests:
        raise ArtifactError('daily requires explicit source requests')
    source_requests=json.loads(_json_bytes(source_requests))
    planned=plan_daily(data_root,concrete,source_requests=source_requests)
    requested=set(planned['source_change_domains'])
    _validate_domain_inputs(domain_inputs,pending_domains=requested)
    from axiom_data.public_source_scope import validate_request_envelope
    for domain, spec in domain_inputs.items():
        if domain in requested:
            params = [r['params'] for r in source_requests if r['domain'] == domain]
            if 'security_session_scope' in spec['config']:
                for raw_id in spec['raw_batch_ids']:
                    raw = load_raw_batch(data_root, raw_id)
                    if raw.manifest['domain'] != domain:
                        raise ArtifactError('source scope Raw domain mismatch')
                    params.append(raw.manifest['request']['params'])
            validate_request_envelope(spec['config'], params)
    if not requested<=set(domain_inputs):
        raise ArtifactError('daily build inputs must cover every required source domain')
    frozen=json.loads(_json_bytes({'source_plan':planned,'domain_inputs':domain_inputs}))
    if observed_raw_batch_ids is not None:
        observed_raw_batch_ids=json.loads(_json_bytes(observed_raw_batch_ids))
        frozen['observed_raw_batch_ids']=observed_raw_batch_ids
    layout=_layout(data_root);directory=layout.root/'operations'/run_id
    with writer(layout.root):
        _ensure_directory(layout.root,directory)
    with open(_safe_path(layout.root,directory/'daily.lock'),'a+b') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise ArtifactError('daily run already active') from exc
        path=_safe_path(layout.root,directory/'daily.json')
        if path.exists() and json.loads(path.read_bytes())['plan']!=frozen:
            raise ArtifactError('resume daily plan differs from frozen inputs')
        state={'schema_version':'daily_run.v1','run_id':run_id,'plan':frozen,
            'plan_digest':_digest(_json_bytes(frozen)),'status':'RUNNING','stage':'COLLECTION',
            'ready_for_consumption':False}
        _save(path,state)
        try:
            collected=collect_requests(layout.root,run_id=run_id,requests=source_requests,client=client,
                                       observed_raw_batch_ids=observed_raw_batch_ids)
            state['collected_raw_batch_ids']=dict(collected['completed'])
            if collected['status']!='COMPLETE':
                state.update(status='FAILED',failed=collected['failed'])
                _save(path,state);return state
            inputs=json.loads(_json_bytes(frozen['domain_inputs']))
            for request in planned['source_requests']:
                raw_id=collected['completed'][request['request_id']]
                ids=inputs[request['domain']]['raw_batch_ids']
                if raw_id not in ids:ids.append(raw_id)
            state.update(stage='CANONICAL_BUILD',resolved_domain_inputs=inputs);_save(path,state)
            result=assemble_candidate(layout.root,run_id=run_id,domain_inputs=inputs,
                                      parent_snapshot_id=concrete)
            state.update(status=result['status'],stage=result['stage'],failed=result['failed'])
            if result['status']=='CANDIDATE_BUILT':
                ids=result['domain_commit_ids'];changed=sorted(d for d,i in ids.items() if i!=planned['parent_commits'][d])
                state.update(snapshot_id=result['snapshot_id'],domain_commit_ids=ids,
                    changed_domains=changed,reused_domains=sorted(set(ids)-set(changed)))
                if not changed:
                    if result['snapshot_id']!=concrete:raise ArtifactError('no-change Snapshot identity mismatch')
                    state['status']='NO_CHANGE'
            _save(path,state);return state
        except Exception as exc:
            state.update(status='FAILED',error_type=type(exc).__name__)
            state.pop('snapshot_id',None);state.pop('domain_commit_ids',None)
            _save(path,state);raise


def bootstrap(data_root, *, run_id, domain_inputs=None, domain_commit_ids=None):
    """Build frozen Raw inputs or resume a complete canonical checkpoint.

    Collection uses collect_bootstrap_sources. Both paths produce an ordinary
    candidate Snapshot and leave required Views/admission outstanding.
    """
    if (domain_inputs is None)==(domain_commit_ids is None):
        raise ArtifactError('bootstrap requires exactly one explicit input mode')
    if domain_inputs is not None:
        return assemble_candidate(data_root,run_id=run_id,domain_inputs=domain_inputs)
    from axiom_data import create_snapshot
    from axiom_data.domains import PR7_SNAPSHOT_DOMAINS
    from axiom_data.build import _validate_identity
    _identity('run_id',run_id)
    if not isinstance(domain_commit_ids,dict) or set(domain_commit_ids)!=set(PR7_SNAPSHOT_DOMAINS):
        raise ArtifactError('bootstrap checkpoint requires all V1 domains')
    ids={d:_validate_identity('domain_commit_id',i) for d,i in domain_commit_ids.items()}
    layout=_layout(data_root)
    with writer(layout.root):
        directory=layout.root/'operations'/run_id;_ensure_directory(layout.root,directory)
        path=_safe_path(layout.root,directory/'bootstrap.json')
        if path.exists() and json.loads(path.read_bytes())['domain_commit_ids']!=ids:
            raise ArtifactError('resume bootstrap checkpoint differs from frozen inputs')
        state={'schema_version':'bootstrap_checkpoint_run.v1','run_id':run_id,
            'domain_commit_ids':ids,'status':'RUNNING','stage':'CANONICAL_CHECKPOINT_VALIDATION',
            'ready_for_consumption':False}
        _save(path,state)
        try:
            ref=create_snapshot(layout.root,ids)
            state.update(status='CANDIDATE_BUILT',stage='REQUIRED_VIEWS_AND_FULL_ADMISSION',
                snapshot_id=ref.snapshot_id,snapshot_manifest_digest=ref.manifest_digest)
        except Exception as exc:
            state.update(status='FAILED',error_type=type(exc).__name__)
        _save(path,state);return state


def repair(data_root, *, run_id, snapshot_id, domain_inputs):
    """Rebuild frozen inputs with the installed builder into a repair candidate.

    Each domain explicitly chooses incremental or clean lineage. Old artifacts
    remain intact; the returned candidate still requires Views and admission.
    """
    from axiom_data.build import _validate_identity
    concrete = _validate_identity('snapshot_id', snapshot_id)
    return assemble_candidate(data_root, run_id=run_id, domain_inputs=domain_inputs,
                              parent_snapshot_id=concrete)


@candidate_verification
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
    _validate_domain_inputs(domain_inputs)
    with writer(layout.root):
        parent_id = _resolve_snapshot_id(layout.root, parent_snapshot_id) if parent_snapshot_id else None
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
                              no_change_policy='reuse_equal_state.v1', coverage_state_policy='source_observations.v1')
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
