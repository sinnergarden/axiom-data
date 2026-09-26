"""Scope-bound Data dependency admission over immutable public artifacts."""
from collections import Counter
from contextlib import nullcontext
from itertools import groupby, islice
from importlib.resources import files
import hashlib
import json

from axiom_data.artifacts import (ArtifactError, _commit_ref, _digest, _ensure_directory,
    _identity, _json_bytes, _layout, _safe_path, load_snapshot)
from axiom_data.admission_plan import validate_admission_plan, requirement_registry_digest, _builders, _config
from axiom_data.consumption import SnapshotReader, exchange_sessions
from axiom_data.domains.market import _checked_security_identity_state
from axiom_data.financial_coverage import financial_batch
from axiom_data.gate_a import make_gate_a_plan
from axiom_data.operations import save_progress
from axiom_data.publication import writer
from axiom_data.recovery import _view_loaders
from axiom_data.source_completeness import requalify_sources
from axiom_data.verification_cache import candidate_verification
from axiom_data.views import _stored_view_kind


def _planned_views(plan):
    return plan['historical_plan']['views'] if plan['schema_version']=='full_admission_plan.v2' else plan['views']


def _checked_plan(root, snapshot_id, plan):
    common={'schema_version','target','scope_registry_digest','snapshot_manifest_digest','view_refs'}
    if not isinstance(plan, dict):
        raise ArtifactError('explicit full admission plan required')
    version=plan.get('schema_version')
    fields=common | ({'historical_plan'} if version=='full_admission_plan.v2' else {'required_view_configs','views'})
    if version not in {'full_admission_plan.v1','full_admission_plan.v2'} or set(plan)!=fields:
        raise ArtifactError('unsupported full admission plan')
    terminal = make_gate_a_plan(plan['target'])['terminal_evidence_plan']
    if plan['scope_registry_digest'] != terminal['scope_registry_digest'] or plan['scope_registry_digest'] != requirement_registry_digest():
        raise ArtifactError('full admission registry mismatch')
    geometry = {k:plan['target'][k] for k in ('symbols', 'start_session', 'end_session')}
    if version=='full_admission_plan.v2':
        from axiom_data.gate_a import plan_historical_views
        reader=SnapshotReader(root,snapshot_id)
        if reader.snapshot.ref.manifest_digest!=plan['snapshot_manifest_digest']:
            raise ArtifactError('full admission Snapshot differs from historical plan')
        historical=plan['historical_plan']
        if not isinstance(historical,dict) or historical.get('schema_version')!='historical_view_execution_plan.v2':
            raise ArtifactError('resolved historical View plan v2 required')
        cutoffs={spec['config'].get('knowledge_cutoff') for spec in historical.get('views',{}).values()
                 if _stored_view_kind(spec['kind']) in {'pr6_fact','pr7_fact'}}
        if len(cutoffs)!=1 or None in cutoffs:
            raise ArtifactError('historical View cutoff must be explicit and uniform')
        actual=plan_historical_views(target=geometry,security_rows=reader.security_master(),
            calendar_rows=reader.trading_calendar(),universe_ids=plan['target']['universe_ids'],
            knowledge_cutoff=cutoffs.pop(),data_root=root,snapshot_id=snapshot_id)
        if historical!=actual or actual['price_anchor_validation']!='RESOLVED':
            raise ArtifactError('historical View plan differs from fixed source/target/anchor evidence')
        checked=actual
    else:
        checked = validate_admission_plan(root, snapshot_id=snapshot_id,
            expected_snapshot_manifest_digest=plan['snapshot_manifest_digest'],
            scope_registry_digest=plan['scope_registry_digest'], target=geometry,
            required_view_configs=plan['required_view_configs'], views=plan['views'])
        if checked['status'] != 'PLAN_VALIDATED':
            raise ArtifactError('full admission target has missing View coverage')
        financial = checked['plan']['required_view_configs']['pr6_fact']
        if set(financial['universe_ids']) != set(plan['target']['universe_ids']):
            raise ArtifactError('full admission universe target differs from View policy')
    if set(plan['view_refs']) != set(_planned_views(plan)):
        raise ArtifactError('full admission exact View refs required')
    return terminal, checked


def _bound_view(reader, spec, ref):
    kind = _stored_view_kind(spec['kind'])
    if set(ref) != {'kind', 'view_id', 'manifest_digest'} or ref['kind'] != kind:
        raise ArtifactError('full admission View reference mismatch')
    view = _view_loaders()[kind](reader.data_root, ref['view_id'], checked_reader=reader)
    manifest = view.manifest
    if (view.ref.manifest_digest != ref['manifest_digest'] or
            manifest['snapshot_ref']['snapshot_id'] != reader.snapshot.ref.snapshot_id):
        raise ArtifactError('full admission View Snapshot mismatch')
    config = _config(_builders()[kind], reader.data_root, reader.snapshot.ref.snapshot_id, spec['config'])
    for key in ('symbols', 'start_session', 'end_session', 'universe_ids', 'industry_system'):
        actual = manifest['scope'].get(key)
        if key == 'universe_ids' and actual is not None:
            actual = sorted(actual)
        if key in config and actual != config[key]:
            raise ArtifactError('full admission View scope mismatch')
    for key in ('pit_policy', 'knowledge_cutoff', 'anchor_session', 'decision_cutoff', 'fields', 'price_basis'):
        if key in config and key in manifest:
            expected, actual = config[key], manifest[key]
            if key == 'knowledge_cutoff':
                from axiom_data.pit import instant
                expected, actual = instant(expected), instant(actual)
            if key == 'fields':
                expected, actual = sorted(expected), sorted(actual)
            if expected != actual:
                raise ArtifactError('full admission View policy mismatch: ' + key)
    return view


def _view_groups(views):
    def key(item):
        spec=item[1]
        return (_stored_view_kind(spec['kind']), _json_bytes({k:v for k,v in spec['config'].items() if k!='symbols'}))
    for _, items in groupby(sorted(views.items(), key=key), key=key):
        while batch := list(islice(items, 50)):
            yield batch


def _fact_state(fact, leaf):
    reason = fact.get('missing_reason')
    # Only temporal absence, after the family's source coverage admission, is
    # interpretable as not-yet-visible. Supplier nulls and ambiguous rows block.
    if fact.get('value') is None and (reason == 'PIT_component_not_visible' or
            reason == 'no_observation_at_cutoff' and leaf.startswith(('forecast.', 'holder.'))):
        return 'NOT_VISIBLE'
    if fact.get('pit_qualification') not in {'verified', 'observed', 'best_effort'}:
        return 'UNKNOWN'
    if reason in {'suspended', 'no_limit', 'outside_security_lifetime'} and fact.get('source_ref'):
        return 'NOT_APPLICABLE'
    if (fact.get('value') is None and reason in {'vendor_null', 'not_provided', 'source_value_missing'}
            and fact.get('source_ref') and fact.get('revision_ref')):
        return 'SOURCE_MISSING'
    if reason or fact.get('value') is None:
        return 'BLOCKED'
    return 'AVAILABLE'


def _financial_request_coverage(reader, target):
    """Check the same observation-window union as Gate A against actual Raw."""
    from axiom_data.artifacts import load_domain_commit, load_raw_batch
    from axiom_data.gate_a import _intervals_cover
    from axiom_data.fundamentals_source import source_date
    pending = [reader.commits['financial_events']]
    seen, raws, intervals = set(), set(), {}
    while pending:
        commit = pending.pop()
        if commit.ref.commit_id in seen:
            continue
        seen.add(commit.ref.commit_id)
        for ref in commit.manifest['ordered_raw_batch_refs']:
            if ref['raw_batch_id'] in raws:
                continue
            raws.add(ref['raw_batch_id'])
            raw = load_raw_batch(reader.data_root, ref['raw_batch_id'])
            request = raw.manifest['request']; params = request['params']
            if request['endpoint'] in {'income','balancesheet','cashflow'} and params.get('report_type') != '1':
                continue
            if 'start_date' in params and 'end_date' in params:
                intervals.setdefault((params.get('ts_code'), request['endpoint']), []).append(
                    (source_date(params['start_date']), source_date(params['end_date'])))
        parent = commit.manifest.get('parent_commit_ref')
        if parent:
            pending.append(load_domain_commit(reader.data_root, 'financial_events', parent['domain_commit_id']))
    for symbol in target['symbols']:
        for endpoint in ('income', 'balancesheet', 'cashflow', 'fina_indicator'):
            if not _intervals_cover(intervals.get((symbol, endpoint), []),
                                    target['financial_observation_start'], target['end_session']):
                raise ArtifactError('financial observation request coverage missing: '+symbol+' '+endpoint)
    return {'start_session':target['financial_observation_start'], 'end_session':target['end_session'],
            'raw_batch_ids':sorted(raws), 'status':'PASS'}


def _assess(root, snapshot_id, plan):
    terminal, geometry = _checked_plan(root, snapshot_id, plan)
    snapshot = load_snapshot(root, snapshot_id)
    if snapshot.ref.manifest_digest != plan['snapshot_manifest_digest']:
        raise ArtifactError('full admission Snapshot changed')
    reader = SnapshotReader(root, snapshot_id)
    requalify_sources(root, {d:c.ref.commit_id for d,c in reader.commits.items()})
    financial_requests = _financial_request_coverage(reader, plan['target'])
    registry = json.loads(files('axiom_data.scope').joinpath('pr7_scope.v1.json').read_bytes())
    leaves = sorted(leaf for group in registry['partition'].values() for leaf in group)
    counts = {leaf:Counter() for leaf in leaves}
    quality_counts = {leaf:Counter() for leaf in leaves}
    hashes = {leaf:hashlib.sha256() for leaf in leaves}
    examples = {leaf:[] for leaf in leaves}
    artifacts = {leaf:set() for leaf in leaves}
    def record(leaf, symbol, session, fact, artifact):
        state = _fact_state(fact, leaf)
        item = dict(symbol=symbol, session=session, state=state, fact=fact)
        hashes[leaf].update(_json_bytes(item))
        counts[leaf][state] += 1
        quality_counts[leaf][str(fact.get('quality_state', fact.get('quality', 'not_declared')))] += 1
        artifacts[leaf].add(artifact)
        if state != 'AVAILABLE' and len(examples[leaf]) < 3:
            examples[leaf].append(item)
    target = plan['target']
    calendars = exchange_sessions(reader, target['symbols'], target['start_session'], target['end_session'])
    identities = {row['symbol']:row for row in reader.security_master()}
    axis = sorted({day for c in calendars.values() for day in c['sessions']})
    bits = {day:1 << i for i,day in enumerate(axis)}
    expected = {s:sum(bits[day] for day in c['sessions'] if
        _checked_security_identity_state(identities[s], day) == 'within_identity_interval')
        for s,c in calendars.items()}
    expected_count = sum(mask.bit_count() for mask in expected.values())
    if not expected_count:
        raise ArtifactError('full admission has no applicable target sessions')
    # One checked closure and one financial preparation per operation. Each
    # materialized shard is consumed and released before opening the next one.
    with financial_batch(reader):
        for group in _view_groups(_planned_views(plan)):
            configs=[spec['config'] for _,spec in group]
            kind=_stored_view_kind(group[0][1]['kind'])
            context=nullcontext()
            if len(group)>1 and all(len(c['symbols'])==1 for c in configs):
                if kind=='pr6_fact':
                    from axiom_data.financial_views import financial_view_batch
                    context=financial_view_batch(reader, configs)
                elif kind=='pr7_fact':
                    from axiom_data.event_views import event_view_batch
                    context=event_view_batch(reader, configs)
            with context:
                for label, spec in group:
                    kind = _stored_view_kind(spec['kind'])
                    view = _bound_view(reader, spec, plan['view_refs'][label])
                    if kind in {'pr6_fact', 'pr7_fact'}:
                        from axiom_data.financial_views import project as financial_project
                        from axiom_data.event_views import project as event_project
                        project = financial_project if kind == 'pr6_fact' else event_project
                        manifest = view.manifest
                        # Older loaders already replay the source projection once.
                        # Current compact loaders validate structure only, so their
                        # value/metadata comparison belongs here exactly once.
                        if manifest['schema_version'] not in {'pr6_fact_view.v1','pr6_fact_view.v2','pr6_fact_view.v3','pr7_fact_view.v1','pr7_fact_view.v2'}:
                            direct = project(reader, manifest['scope'], manifest['pit_policy'], manifest['knowledge_cutoff'])
                            if _json_bytes(list(view.rows)) != _json_bytes(direct['wide']):
                                raise ArtifactError('full admission direct/View metadata mismatch')
                        for row in view.rows:
                            if not expected[row['symbol']] & bits[row['session']]:
                                continue
                            for leaf, fact in row['facts'].items():
                                record(leaf, row['symbol'], row['session'], fact, view.ref.view_id)
                    elif kind == 'adjusted_price':
                        scope=view.manifest['scope']
                        keyed={(r['symbol'],r['session']):r for r in view.rows}
                        statuses={(r['symbol'],r['session']):r for r in reader.facts(
                            'security_status', symbols=scope['symbols'],
                            start_session=scope['start_session'],end_session=scope['end_session'])}
                        for symbol in scope['symbols']:
                            for day in axis:
                                if not (scope['start_session']<=day<=scope['end_session'] and expected[symbol]&bits[day]):
                                    continue
                                row=keyed.get((symbol,day));status=statuses.get((symbol,day),{})
                                reason='missing_required_row' if row is None else None if row['adjustment_state']=='ok' else row['adjustment_state']
                                if reason in {'missing_required_row','no_market_price'} and status.get('status')=='suspended':
                                    reason='suspended'
                                record('adjusted_price.anchored_ohlc',symbol,day,
                                    dict(value=None if row is None else [row[f] for f in ('open','high','low','close')],
                                         missing_reason=reason, source_ref=status.get('source_ref'),
                                         pit_qualification=view.manifest['pit_qualification']),view.ref.view_id)
    # Consume canonical daily inputs once per month, not once per security or
    # session. Retain only that month's key index; never full-history payloads.
    domains = {}
    for leaf, spec in registry['pr5_public_evidence'].items():
        if spec['domain'] != 'adjusted_price':
            domains.setdefault(spec['domain'], []).append((leaf, spec['field']))
    all_days = [day for day in axis if any(mask & bits[day] for mask in expected.values())]
    months = {}
    for day in all_days:
        months.setdefault(day[:7], []).append(day)
    for domain, bindings in domains.items():
        commit = reader.commits[domain]
        for days in months.values():
            rows = reader.facts(domain, start_session=days[0], end_session=days[-1],
                **({} if domain in {'trading_calendar','benchmark_daily'} else {'symbols':target['symbols']}))
            statuses = {(r['session'],r['symbol']):r for r in reader.facts(
                'security_status',symbols=target['symbols'],start_session=days[0],end_session=days[-1])} if domain=='market_daily' else {}
            keyed = {(r['session'],r.get('symbol',r.get('benchmark',r.get('exchange')))):r for r in rows}
            for day in days:
                symbols = target['benchmarks'] if domain == 'benchmark_daily' else [s for s in expected if expected[s] & bits[day]]
                for symbol in symbols:
                    key = identities[symbol]['exchange'] if domain == 'trading_calendar' else symbol
                    row = keyed.get((day,key))
                    for leaf, field in bindings:
                        fact = dict(value=row.get(field) if row else None,
                            missing_reason='missing_required_row' if row is None else
                                ('suspended' if row.get('is_suspended') else 'no_limit' if row.get('limit_state')=='no_limit' else row.get('missing_reason')) if row.get(field) is None else None,
                            pit_qualification=row.get('pit_qualification', 'best_effort') if row else 'unknown',
                            source_ref=row.get('source_ref') if row else None,
                            domain_commit_id=commit.ref.commit_id, contract_version=commit.ref.contract_version)
                        if row is None and statuses.get((day,symbol),{}).get('status')=='suspended':
                            fact.update(missing_reason='suspended',source_ref=statuses[(day,symbol)]['source_ref'])
                        record(leaf, symbol, day, fact, commit.ref.commit_id)
    requirements = []
    for leaf in leaves:
        expected_rows = len(all_days)*len(target['benchmarks']) if leaf == 'benchmark.close' else expected_count
        count = counts[leaf]
        complete = sum(count.values()) == expected_rows
        requirements.append(dict(requirement=leaf, status='PASS' if complete and not
            (count['BLOCKED'] or count['UNKNOWN']) else 'BLOCKED',
            expected_rows=expected_rows, inspected_rows=sum(count.values()), states=dict(count),
            original_quality_states=dict(quality_counts[leaf]),
            value_availability='COMPLETE' if count['AVAILABLE']==expected_rows else 'PARTIAL_OR_UNAVAILABLE',
            logical_digest='sha256:'+hashes[leaf].hexdigest(), artifact_refs=sorted(artifacts[leaf]),
            missing_examples=examples[leaf]))
    passed = {r['requirement'] for r in requirements if r['status']=='PASS'}
    available = {r['requirement'] for r in requirements if r['value_availability']=='COMPLETE'}
    features = [dict(f, status='PASS' if set(f['leaves']) <= passed else 'BLOCKED',
        value_availability='DEPENDENCY_VALUES_PRESENT' if set(f['leaves']) <= available else 'DEPENDENCY_VALUES_MISSING')
                for f in registry['feature_dependencies']]
    return dict(schema_version='full_admission_report.v1', root=str(_layout(root).root.resolve()),
        snapshot_id=snapshot_id, snapshot_manifest_digest=snapshot.ref.manifest_digest,
        target_digest=terminal['target_digest'], scope_registry_digest=plan['scope_registry_digest'],
        plan=plan, plan_digest=_digest(_json_bytes(plan)), view_refs=plan['view_refs'],
        requirements=requirements, features=features,
        source_evidence_refs={d:_commit_ref(c) for d,c in reader.commits.items()},
        financial_observation_coverage=financial_requests,
        status='PASS' if len(passed)==56 else 'BLOCKED', ready_for_consumption=False,
        warnings=['Source missing values remain unavailable to consumers'] if any(
            c['SOURCE_MISSING'] for c in counts.values()) else [],
        qualification='Data dependency admission only; explicit PIT and missing states; independent review required')


@candidate_verification
def full_admission(root, *, run_id, snapshot_id, plan):
    """Inspect the entire declared target and persist its admission report."""
    root = _layout(root).root
    _identity('run_id', run_id)
    plan = json.loads(_json_bytes(plan))
    with writer(root):
        path = _safe_path(root, root/'operations'/run_id/'full-admission.json')
        if path.exists():
            stored = json.loads(path.read_bytes())
            if stored['plan'] != plan or stored['snapshot_id'] != snapshot_id:
                raise ArtifactError('full admission resume plan differs')
        result = dict(_assess(root, snapshot_id, plan), run_id=run_id)
        _ensure_directory(root, path.parent)
        save_progress(path, result)
    return result


@candidate_verification
def validate_full_admission(root, report, *, expected_views, target_digest):
    """Reload the durable report and re-inspect every fixed input and result."""
    root = _layout(root).root
    run_id = _identity('run_id', report['run_id'])
    stored = json.loads(_safe_path(root, root/'operations'/run_id/'full-admission.json').read_bytes())
    if stored != report or report['view_refs'] != expected_views or report['target_digest'] != target_digest:
        raise ArtifactError('full admission report/ref/target mismatch')
    actual = dict(_assess(root, report['snapshot_id'], report['plan']), run_id=run_id)
    if actual != stored or actual['status'] != 'PASS':
        raise ArtifactError('full admission inputs differ or target is blocked')
    return dict(status='FULL_ADMISSION_VALIDATED', snapshot_id=report['snapshot_id'],
                target_digest=target_digest, ready_for_consumption=False)
