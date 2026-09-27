"""Read-only coverage preflight for an explicit V1 required-View plan.

Only the Snapshot manifest and identity/calendar closures are checked here.
Source availability, View construction and full Data admission remain separate.
"""
import inspect
import json
from importlib.resources import files
from types import SimpleNamespace

from axiom_data.artifacts import (
    ArtifactError, _commit_ref, _digest, _identity, _json_bytes, _layout,
    _load_manifest, _timestamp, _validate_manifest_identity, validate_domain_commit_closure,
)
from axiom_data.build import _validate_identity
from axiom_data.consumption import MARKET_VIEW_FIELDS, validate_session, validate_symbols
from axiom_data.domains.market import _checked_security_identity_state
from axiom_data.domains import EVENT_SNAPSHOT_DOMAINS
from axiom_data.pit import POLICIES, instant
from axiom_data.consumption import exchange_sessions
from axiom_data.views import _stored_view_kind


def requirement_registry_digest():
    """Digest of the frozen Data requirement registry used by plan admission."""
    return _digest(files('axiom_data.scope').joinpath('pr7_scope.v1.json').read_bytes())


def _builders():
    from axiom_data import build_adjusted_price_view, build_market_replay_view, build_qlib_view
    from axiom_data.financial_views import build_financial_fact_view
    from axiom_data.event_views import build_event_fact_view
    return dict(adjusted_price=build_adjusted_price_view, market_replay=build_market_replay_view,
                market_qlib=build_qlib_view, pr6_fact=build_financial_fact_view, pr7_fact=build_event_fact_view)


def _config(builder, root, snapshot_id, config):
    if not isinstance(config, dict):
        raise ArtifactError('View config must be an object')
    try:
        bound = inspect.signature(builder).bind(root, snapshot_id, **config)
    except TypeError as exc:
        raise ArtifactError('invalid required View arguments') from exc
    bound.apply_defaults()
    result = dict(bound.arguments)
    if result.get('created_at') is not None:
        _timestamp(result['created_at'])
    for key in ('data_root', 'snapshot_id', 'created_at'):
        result.pop(key, None)
    result['symbols'] = list(validate_symbols(result['symbols']))
    start = validate_session(result['start_session'], 'start_session')
    end = validate_session(result['end_session'], 'end_session')
    result.update(start_session=start, end_session=end)
    if start > end:
        raise ArtifactError('reversed View scope')
    if 'knowledge_cutoff' in result:
        result['knowledge_cutoff'] = instant(result['knowledge_cutoff']).isoformat()
        if result['pit_policy'] not in POLICIES:
            raise ArtifactError('invalid fact PIT policy')
    if 'universe_ids' in result:
        groups = result['universe_ids']
        if not isinstance(groups, (list, tuple)) or not groups or len(set(groups)) != len(groups):
            raise ArtifactError('nonempty unique universe IDs required')
        result['universe_ids'] = sorted(_identity('universe_id', g) for g in groups)
        _identity('industry_system', result['industry_system'])
    if 'anchor_session' in result:
        anchor = validate_session(result['anchor_session'], 'anchor_session')
        cutoff = validate_session(result['decision_cutoff'], 'decision_cutoff')
        result.update(anchor_session=anchor, decision_cutoff=cutoff)
        if not start <= anchor <= end:
            raise ArtifactError('anchor outside View scope')
        if result['pit_policy'] not in {'research_non_pit', 'strict_decision_time'}:
            raise ArtifactError('invalid adjusted PIT policy')
        if result['pit_policy'] == 'strict_decision_time' and anchor > cutoff:
            raise ArtifactError('future adjusted anchor')
    if 'fields' in result:
        fields = result['fields']
        if (not isinstance(fields, (list, tuple)) or not fields or len(fields) != len(set(fields))
                or not set(fields) <= set(MARKET_VIEW_FIELDS)):
            raise ArtifactError('invalid Qlib fields')
        result['fields'] = sorted(fields)
        # V1 has a separate anchored-price View. Keep this gate's market export
        # unadjusted rather than introduce unresolved cross-shard Derived refs.
        if (result['price_basis'] != 'unadjusted' or result['adjusted_price_view_id'] is not None
                or result['pit_policy'] != 'best_effort' or result['decision_cutoff'] is not None):
            raise ArtifactError('V1 plan requires unadjusted best_effort market Qlib')
    return result


def validate_admission_plan(data_root, *, snapshot_id, expected_snapshot_manifest_digest,
                            scope_registry_digest, target, required_view_configs, views):
    """Check planned coverage without building Views or claiming source admission.

    target is the complete requested symbols/start/end rectangle. Each of the
    five required_view_configs declares its full semantic config without those
    three scope keys. views uses materialize_views' existing kind/config shape.
    No unavailable declarations can waive a missing planned session.
    """
    snapshot_id = _validate_identity('snapshot_id', snapshot_id)
    registry_bytes = files('axiom_data.scope').joinpath('pr7_scope.v1.json').read_bytes()
    if scope_registry_digest != _digest(registry_bytes):
        raise ArtifactError('frozen requirement registry digest mismatch')
    registry = json.loads(registry_bytes)
    leaves = [leaf for group in registry['partition'].values() for leaf in group]
    features = registry['feature_dependencies']
    if (len(leaves) != 56 or len(set(leaves)) != 56 or len(features) != 469
            or any(not set(f['leaves']) <= set(leaves) for f in features)):
        raise ArtifactError('invalid packaged requirement registry')
    if not isinstance(target, dict) or set(target) != {'symbols', 'start_session', 'end_session'}:
        raise ArtifactError('complete explicit target required')
    target = dict(target, symbols=list(validate_symbols(target['symbols'])))
    start = validate_session(target['start_session'], 'start_session')
    end = validate_session(target['end_session'], 'end_session')
    target.update(start_session=start, end_session=end)
    if start > end:
        raise ArtifactError('reversed target scope')
    builders = _builders()
    if not isinstance(required_view_configs, dict):
        raise ArtifactError('all five required View kinds must declare target semantics')
    canonical_configs = {_stored_view_kind(kind): config for kind, config in required_view_configs.items()}
    if len(canonical_configs) != len(required_view_configs) or set(canonical_configs) != set(builders):
        raise ArtifactError('all five required View kinds must declare target semantics')
    required_view_configs = canonical_configs
    required = {}
    for kind, config in required_view_configs.items():
        if not isinstance(config, dict) or set(config) & set(target):
            raise ArtifactError('required View config must not override target scope')
        required[kind] = _config(builders[kind], data_root, snapshot_id, dict(config, **target))
    if set(required['market_qlib']['fields']) != set(MARKET_VIEW_FIELDS):
        raise ArtifactError('V1 target requires the complete market Qlib field set')
    if not isinstance(views, dict) or not views:
        raise ArtifactError('nonempty explicit View shards required')
    normalized = {}
    for label, spec in views.items():
        _identity('view_label', label)
        if (not isinstance(spec, dict) or set(spec) != {'kind', 'config'}
                or not isinstance(spec['kind'], str)):
            raise ArtifactError('invalid View shard')
        kind = _stored_view_kind(spec['kind'])
        if kind not in builders:
            raise ArtifactError('invalid View shard')
        config = _config(builders[kind], data_root, snapshot_id, spec['config'])
        if not set(config['symbols']) <= set(target['symbols']):
            raise ArtifactError('View shard security outside target')
        if not start <= config['start_session'] <= config['end_session'] <= end:
            raise ArtifactError('View shard dates outside target')
        semantic = lambda c: {k: v for k, v in c.items() if k not in target}
        if semantic(config) != semantic(required[kind]):
            raise ArtifactError('View shard semantics differ from required target')
        normalized[label] = dict(kind=kind, config=config)

    layout = _layout(data_root)
    manifest, digest = _load_manifest(
        layout.root, layout.snapshots / snapshot_id, artifact_type='data_snapshot',
        schema_version='data_snapshot.v4', identity_field='snapshot_id', identity=snapshot_id)
    _validate_manifest_identity(manifest, 'snapshot_id', 'snapshot', snapshot_id)
    if digest != expected_snapshot_manifest_digest:
        raise ArtifactError('Snapshot manifest differs from frozen reference')
    if not isinstance(manifest.get('created_at'), str):
        raise ArtifactError('Snapshot created_at is missing')
    _timestamp(manifest['created_at'])
    refs = manifest.get('domain_refs')
    if (not isinstance(refs, dict) or set(refs) != set(EVENT_SNAPSHOT_DOMAINS)
            or manifest.get('validation_summary') != {
                'status': 'PASS', 'required_domains': list(EVENT_SNAPSHOT_DOMAINS), 'cross_domain': 'PASS'}):
        raise ArtifactError('incomplete Snapshot manifest composition')
    for domain, ref in refs.items():
        if not isinstance(ref, dict) or ref.get('domain') != domain:
            raise ArtifactError('invalid Snapshot domain reference')
        _validate_identity('domain_commit_id', ref.get('domain_commit_id'))
    reference = {}
    for domain in ('security_master', 'trading_calendar'):
        ref = manifest['domain_refs'][domain]
        commit = validate_domain_commit_closure(layout.root, domain, ref['domain_commit_id'])
        if _commit_ref(commit) != ref:
            raise ArtifactError('Snapshot reference-domain binding mismatch')
        reference[domain] = commit
    reader = SimpleNamespace(security_master=lambda: reference['security_master'].rows,
                             trading_calendar=lambda: reference['trading_calendar'].rows)
    calendars = exchange_sessions(reader, target['symbols'], start, end)
    identities = {r['symbol']: r for r in reader.security_master()}
    anchor = required['adjusted_price']['anchor_session']
    for symbol, entry in calendars.items():
        if (anchor not in entry['sessions']
                or _checked_security_identity_state(identities[symbol], anchor) != 'within_identity_interval'):
            raise ArtifactError('adjusted anchor outside security identity/open sessions')
    axis = sorted({d for entry in calendars.values() for d in entry['sessions']})
    bits = {day: 1 << i for i, day in enumerate(axis)}
    expected = {}
    for symbol, entry in calendars.items():
        mask = 0
        if identities[symbol]['list_session'] is None:
            raise ArtifactError('unknown security identity interval')
        for day in entry['sessions']:
            if _checked_security_identity_state(identities[symbol], day) == 'within_identity_interval':
                mask |= bits[day]
        expected[symbol] = mask
    coverage = {kind: {s: 0 for s in expected} for kind in builders}
    for label, spec in normalized.items():
        config = spec['config']; kind = spec['kind']
        mask = sum(bits[d] for d in axis if config['start_session'] <= d <= config['end_session'])
        active = False
        for symbol in config['symbols']:
            selected = expected[symbol] & mask
            if selected & coverage[kind][symbol]:
                raise ArtifactError('duplicate overlapping View shard work: ' + label)
            coverage[kind][symbol] |= selected
            active |= bool(selected)
        if not active:
            raise ArtifactError('View shard has no eligible security sessions')
    gaps = []
    for kind, symbols in coverage.items():
        for symbol, mask in expected.items():
            missing = mask & ~symbols[symbol]
            if missing:
                gaps.append({'kind': kind, 'symbol': symbol, 'missing_sessions': missing.bit_count(),
                             'first_missing_session': next(d for d in axis if bits[d] & missing)})
    canonical_views = {label: dict(spec, kind=_stored_view_kind(spec['kind'])) for label, spec in views.items()}
    plan = dict(snapshot_id=snapshot_id, expected_snapshot_manifest_digest=digest,
                scope_registry_digest=scope_registry_digest, target=target,
                required_view_configs=required_view_configs, views=canonical_views)
    return {'schema_version': 'admission_plan_check.v1', 'status': 'INCOMPLETE' if gaps else 'PLAN_VALIDATED',
            'plan_digest': _digest(_json_bytes(plan)), 'plan': plan,
            'reference_domain_refs': {d: _commit_ref(c) for d, c in reference.items()},
            'registry_requirement_count': len(leaves), 'registry_feature_count': len(features),
            'expected_sessions_by_symbol': {s: m.bit_count() for s, m in expected.items()},
            'missing_shard_coverage': gaps, 'snapshot_closure_validation': 'PENDING',
            'source_availability_validation': 'PENDING', 'view_payload_validation': 'PENDING',
            'admission': 'NOT_ASSESSED', 'ready_for_consumption': False}


# Compatibility exports for historical callers.
PR7_SNAPSHOT_DOMAINS = EVENT_SNAPSHOT_DOMAINS


class PriceAnchorError(ArtifactError):
    """A price preflight failure tied to its requested View."""
    def __init__(self, label, reason):
        self.view_label = label
        super().__init__(f'{label}: {reason}')


def resolve_price_anchors(reader, views, *, historical=False):
    """Resolve nonstrict historical anchors, or preflight explicit anchors.

    One trusted partition scan retains only candidate anchors, not full history.
    Coverage and other View families remain separate admission requirements.
    """
    from copy import deepcopy
    from axiom_data.domains.reference import validate_strict_decision_time, validate_adjustment_factor_rows
    from axiom_data.domains import MarketContractError
    resolved = deepcopy(views)
    requests = {}
    for label, spec in resolved.items():
        if _stored_view_kind(spec['kind']) != 'adjusted_price':
            continue
        try:
            config = _config(_builders()['adjusted_price'], reader.data_root,
                             reader.snapshot.ref.snapshot_id, spec['config'])
        except ArtifactError as exc:
            raise PriceAnchorError(label, str(exc)) from exc
        requests[label] = config
    if not requests:
        return resolved, {}
    start = min(c['start_session'] for c in requests.values())
    end = max(c['anchor_session'] for c in requests.values())
    symbols = sorted({s for c in requests.values() for s in c['symbols']})
    calendars = exchange_sessions(reader, symbols, start, end)
    identities = {r['symbol']: r for r in reader.security_master()}
    sessions = {s: set(c['sessions']) for s, c in calendars.items()}
    by_symbol = {s: [] for s in symbols}
    for label, config in requests.items():
        for symbol in config['symbols']:
            by_symbol[symbol].append((label, config))
    anchors = {}
    for row in reader.session_rows('adjustment_factors', start, end):
        symbol, day = row['symbol'], row['session']
        relevant = [(label, config) for label, config in by_symbol.get(symbol, ())
                    if config['start_session'] <= day <= config['anchor_session']]
        if relevant:
            try:
                validate_adjustment_factor_rows([row])
            except MarketContractError as exc:
                raise PriceAnchorError(relevant[0][0], f'invalid factor {symbol}/{day}: {exc}') from exc
        for label, config in relevant:
            search = historical and config['pit_policy'] == 'research_non_pit'
            if (config['start_session'] <= day <= config['anchor_session'] and
                    (search or day == config['anchor_session']) and day in sessions[symbol] and
                    _checked_security_identity_state(identities[symbol], day) == 'within_identity_interval'):
                key = (label, symbol)
                if key not in anchors or day > anchors[key]['session']:
                    anchors[key] = row
    evidence = {}
    for label, config in requests.items():
        rows = [anchors.get((label, s)) for s in config['symbols']]
        if any(r is None for r in rows):
            raise PriceAnchorError(label, 'adjusted-price anchor factor is unavailable')
        if config['pit_policy'] == 'strict_decision_time':
            try:
                validate_strict_decision_time(rows, config['decision_cutoff'])
            except MarketContractError as exc:
                raise PriceAnchorError(label, str(exc)) from exc
        days = {r['session'] for r in rows}
        if len(days) != 1:
            raise PriceAnchorError(label, 'historical anchor resolution requires per-security View shards')
        actual = days.pop()
        resolved[label]['config']['anchor_session'] = actual
        evidence[label] = dict(requested_anchor_upper_bound=config['anchor_session'],
            actual_anchor_session=actual, factor_rows=rows,
            selection_rule=('last_observed_factor_in_applicable_interval.v1'
                            if historical and config['pit_policy'] == 'research_non_pit' else 'explicit_anchor.v1'))
    # Resolve actual source artifacts once across all chosen anchors. Parent
    # commits are checked with the existing loader, without mapping replay.
    from axiom_data.artifacts import _load_domain_commit, load_raw_batch, _raw_ref, _validate_dm1_observation_refs
    commit = reader.commits['adjustment_factors']
    raw_refs = {}
    visited = set()
    required_sources = {row['source_ref'] for row in anchors.values()}
    while commit.ref.commit_id not in visited:
        visited.add(commit.ref.commit_id)
        raw_refs.update({ref['raw_batch_id']: ref for ref in commit.manifest['ordered_raw_batch_refs']})
        parent = commit.manifest['parent_commit_ref']
        if required_sources <= raw_refs.keys() or parent is None:
            break
        commit = _load_domain_commit(reader.data_root, 'adjustment_factors', parent['domain_commit_id'], verify_rows=False)
        if _commit_ref(commit) != parent:
            raise ArtifactError('factor parent reference mismatch')
    from datetime import datetime
    verified_sources = {}
    for (label, symbol), row in anchors.items():
        try:
            source = row['source_ref']
            if source not in raw_refs:
                raise ArtifactError('factor source_ref outside DomainCommit Raw lineage')
            if source not in verified_sources:
                raw = load_raw_batch(reader.data_root, source)
                if raw.manifest['domain'] != 'adjustment_factors' or _raw_ref(raw) != raw_refs[source]:
                    raise ArtifactError('factor Raw source reference mismatch')
                verified_sources[source] = (raw.manifest['domain'], source,
                    datetime.fromisoformat(raw.manifest['retrieved_at'].replace('Z', '+00:00')))
            _validate_dm1_observation_refs(reader.data_root, 'adjustment_factors', [row], set(raw_refs),
                                           verified_evidence=verified_sources)
        except ArtifactError as exc:
            raise PriceAnchorError(label, str(exc)) from exc
    reader._check_consumed_metadata(force=True)
    return resolved, dict(schema_version='price_anchor_resolution.v1',
        snapshot_id=reader.snapshot.ref.snapshot_id,
        snapshot_manifest_digest=reader.snapshot.ref.manifest_digest,
        factor_domain_ref=reader.snapshot.manifest['domain_refs']['adjustment_factors'], anchors=evidence)


def _preflight_items(reader, views):
    """Assess every deterministic prerequisite before the first publication.

    Projection/value acceptance remains in the builders and full admission.
    Financial preparation is shared by the enclosing operation context.
    """
    from axiom_data.views import _prepare_adjusted_price_view
    from axiom_data.consumption import _validate_qlib_inputs, _qlib_geometry
    from axiom_data.financial_views import admit_financial_view
    from axiom_data.event_views import admit_event_view
    results = {}
    for label, spec in views.items():
        kind, config = spec['kind'], spec['config']
        try:
            builder = _builders()[kind]
            bound = inspect.signature(builder).bind(reader.data_root, reader.snapshot.ref.snapshot_id, **config)
            bound.apply_defaults()
            args = dict(bound.arguments)
            args.pop('data_root'); args.pop('snapshot_id')
            if args.get('created_at') is not None:
                _timestamp(args['created_at'])
            symbols = validate_symbols(args['symbols'])
            start = validate_session(args['start_session'], 'start_session')
            end = validate_session(args['end_session'], 'end_session')
            if start > end:
                raise ArtifactError('reversed View scope')
            if kind == 'adjusted_price':
                _prepare_adjusted_price_view(reader, **args)
            elif kind == 'market_qlib':
                selected, start, end, _, _ = _validate_qlib_inputs(reader, **args, structural_only=True)
                _qlib_geometry(reader, selected, start, end)
                from axiom_data.consumption import prepared_market_view_rows
                if prepared_market_view_rows(reader,'market_qlib','market_daily',selected,start,end) is None:
                    reader.market_daily(selected,start,end)
            elif kind == 'market_replay':
                from axiom_data.views import _prepare_market_replay_view
                _prepare_market_replay_view(reader, **args)
            else:
                if args['pit_policy'] not in POLICIES:
                    raise ArtifactError('invalid fact PIT policy')
                instant(args['knowledge_cutoff'])
                scope = {k:v for k,v in args.items() if k not in {'pit_policy','knowledge_cutoff'}}
                if kind == 'pr6_fact':
                    admit_financial_view(reader, scope, args['pit_policy'], args['knowledge_cutoff'])
                else:
                    admit_event_view(reader, scope, args['pit_policy'], args['knowledge_cutoff'])
            results[label] = {'status':'READY', 'scope':'deterministic_prerequisites'}
        except Exception as exc:
            results[label] = {'status':'BLOCKED', 'stage':'view_preflight',
                              'error_type':type(exc).__name__, 'reason':str(exc)}
    return results


def preflight_views(reader, views):
    from contextlib import nullcontext
    from axiom_data.view_operation import view_batches
    from axiom_data.views import adjusted_price_batch
    from axiom_data.consumption import market_view_batch
    from axiom_data.financial_views import financial_view_batch
    from axiom_data.event_views import event_view_batch
    results = {}
    for group in view_batches(views):
        kind = group[0][1]['kind']
        configs = [spec['config'] for _,spec in group]
        try:
            context = nullcontext()
            if len(group)>1 and all(isinstance(c.get('symbols'),list) and len(c['symbols'])==1 for c in configs):
                context = (adjusted_price_batch(reader,configs) if kind=='adjusted_price' else
                    market_view_batch(reader,configs,kind) if kind in {'market_replay','market_qlib'} else
                    financial_view_batch(reader,configs) if kind=='pr6_fact' else event_view_batch(reader,configs))
            with context:
                results.update(_preflight_items(reader,dict(group)))
        except Exception as exc:
            for label,_ in group:
                results[label] = {'status':'BLOCKED','stage':'shared_preparation',
                    'error_type':type(exc).__name__,'reason':str(exc),
                    'scope':{'kind':kind,'labels':[k for k,_ in group]}}
    return results
