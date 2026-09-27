"""Resumable required-View stage over the existing immutable public builders."""

from axiom_data.frozen_execution import frozen_operation, bind_view_execution
import inspect
import json
import time
from contextlib import nullcontext
from functools import partial
from importlib.resources import files

from axiom_data.artifacts import ArtifactError, _identity, _layout, _ensure_directory, _safe_path, _digest, _json_bytes
from axiom_data.build import _validate_identity
from axiom_data.operations import save_progress
from axiom_data.publication import writer


def _completed_view(reader, spec, record, builder, loader, code, *, return_view=False):
    """Validate the artifact itself and bind its declared inputs to this request."""
    reader._check_consumed_metadata(force=True)
    from axiom_data.consumption import validate_symbols
    from axiom_data.pit import instant
    if record['kind'] != spec['kind']:
        raise ArtifactError('completed View kind mismatch')
    view = loader(reader.data_root, record['view_id'], checked_reader=reader)
    if view.ref.manifest_digest != record['manifest_digest']:
        raise ArtifactError('completed View manifest mismatch')
    manifest = view.manifest
    args = inspect.signature(builder).bind(reader.data_root, reader.snapshot.ref.snapshot_id, **spec['config'])
    args.apply_defaults()
    config = args.arguments
    if spec['kind'] == 'market_qlib':
        from axiom_data.consumption import _validate_qlib_inputs
        _validate_qlib_inputs(reader,structural_only=True,
            **{k:v for k,v in config.items() if k not in {'data_root','snapshot_id'}})
        actual_basis = ('unadjusted' if manifest['schema_version'] in {'qlib_view.v1','qlib_view.v3'}
                        else manifest['price_basis'])
        if actual_basis != config['price_basis']:
            raise ArtifactError('completed Qlib View price basis mismatch')
    scope = manifest['scope']
    for key in ('symbols', 'start_session', 'end_session', 'universe_ids', 'industry_system'):
        if key in config:
            expected = list(validate_symbols(config[key])) if key == 'symbols' else config[key]
            if scope.get(key) != expected:
                raise ArtifactError('completed View scope mismatch')
    if spec['kind'] in {'pr6_fact', 'pr7_fact'}:
        if manifest['implementation_digests'] != code:
            raise ArtifactError('completed View implementation mismatch')
        if instant(manifest['knowledge_cutoff']) != instant(config['knowledge_cutoff']):
            raise ArtifactError('completed View cutoff mismatch')
    for key in ('anchor_session', 'pit_policy', 'decision_cutoff', 'fields', 'price_basis'):
        # Unadjusted Qlib v1 does not declare policy/cutoff: those inputs have
        # no effect in its existing builder. The frozen plan still binds them.
        expected = list(config[key]) if key == 'fields' and key in config else config.get(key)
        if key in config and key in manifest and manifest[key] != expected:
            raise ArtifactError('completed View input mismatch: ' + key)
    if spec['kind'] == 'market_qlib':
        refs = manifest.get('derived_refs', [])
        if [r['view_id'] for r in refs] != ([config['adjusted_price_view_id']] if config['adjusted_price_view_id'] else []):
            raise ArtifactError('completed View Derived input mismatch')
    return view if return_view else view.ref


def view_batches(views):
    """One batch definition for prerequisite admission and actual execution."""
    from axiom_data.views import ADJUSTED_BATCH_SIZE
    from axiom_data.consumption import MARKET_BATCH_SIZE
    from axiom_data.financial_views import FINANCIAL_VIEW_BATCH_SIZE
    from axiom_data.event_views import EVENT_BATCH_SIZE
    bounds = {'adjusted_price': ADJUSTED_BATCH_SIZE, 'market_replay': MARKET_BATCH_SIZE,
              'market_qlib': MARKET_BATCH_SIZE, 'pr6_fact': FINANCIAL_VIEW_BATCH_SIZE,
              'pr7_fact': EVENT_BATCH_SIZE}
    group = []
    symbols = set()
    for label, spec in views.items():
        selected = spec['config'].get('symbols')
        singleton = (isinstance(selected, (list, tuple)) and len(selected) == 1
                     and isinstance(selected[0], str))
        compatible = False
        if group and singleton:
            first = group[0][1]
            keys = (('start_session', 'end_session', 'universe_ids', 'industry_system',
                     'pit_policy', 'knowledge_cutoff') if spec['kind'] == 'pr6_fact' else
                    ('start_session', 'end_session', 'pit_policy', 'knowledge_cutoff')
                    if spec['kind'] == 'pr7_fact' else ())
            compatible = (spec['kind'] == first['kind'] and len(group) < bounds[spec['kind']]
                and selected[0] not in symbols
                and all(spec['config'].get(key) == first['config'].get(key) for key in keys))
        if group and not compatible:
            yield group
            group = []
            symbols = set()
        group.append((label, spec))
        if singleton:
            symbols.add(selected[0])
        else:
            yield group
            group = []
            symbols = set()
    if group:
        yield group


@frozen_operation('views')
def materialize_views(data_root, *, run_id, snapshot_id, views):
    """Build frozen View requests; required failures keep the candidate unready.

    Resume validates completed artifacts before entering a builder.
    The execution record never substitutes for artifact identity or validation.
    """
    from axiom_data.views import (adjusted_price_batch, build_adjusted_price_view,
                                  build_market_replay_view, _stored_view_kind)
    from axiom_data.consumption import build_qlib_view, market_view_batch
    from axiom_data.financial_views import build_financial_fact_view, financial_view_batch
    from axiom_data.event_views import build_event_fact_view, event_view_batch
    from axiom_data.views import _build_adjusted_price_view, _build_market_replay_view
    from axiom_data.consumption import SnapshotReader, _build_qlib_view
    from axiom_data.financial_views import build_financial_fact_view_from_reader
    from axiom_data.event_views import build_event_fact_view_from_reader
    from axiom_data.views import _load_adjusted_price_view, _load_market_replay_view
    from axiom_data.consumption import _load_qlib_view
    from axiom_data.financial_views import load_financial_fact_view_with_reader
    from axiom_data.event_views import load_event_fact_view_with_reader
    loaders={'adjusted_price':partial(_load_adjusted_price_view,structural_only=True),
        'market_replay':partial(_load_market_replay_view,structural_only=True),
        'market_qlib':partial(_load_qlib_view,structural_only=True),
        'pr6_fact':load_financial_fact_view_with_reader,'pr7_fact':load_event_fact_view_with_reader}
    builders={'adjusted_price':build_adjusted_price_view,'market_replay':build_market_replay_view,
              'market_qlib':build_qlib_view,'pr6_fact':build_financial_fact_view,'pr7_fact':build_event_fact_view}
    checked_builders={'adjusted_price':_build_adjusted_price_view,'market_replay':_build_market_replay_view,
        'market_qlib':_build_qlib_view,'pr6_fact':build_financial_fact_view_from_reader,'pr7_fact':build_event_fact_view_from_reader}
    concrete=_validate_identity('snapshot_id',snapshot_id);_identity('run_id',run_id)
    if not isinstance(views,dict) or not views:raise ArtifactError('explicit nonempty required View plan required')
    frozen=json.loads(_json_bytes(views))
    for label,spec in frozen.items():
        _identity('view_label',label)
        if not isinstance(spec,dict) or not {'kind','config'} <= set(spec) or set(spec)-{'kind','config','reuse_candidate'} or not isinstance(spec['kind'],str) or not isinstance(spec['config'],dict):
            raise ArtifactError('required View needs a registered kind and config')
        spec['kind'] = _stored_view_kind(spec['kind'])
        if spec['kind'] not in builders:
            raise ArtifactError('required View needs a registered kind and config')
        try:inspect.signature(builders[spec['kind']]).bind(data_root,concrete,**spec['config'])
        except TypeError as exc:raise ArtifactError('invalid required View arguments: ' + label) from exc
    source=files('axiom_data')
    code={p.relative_to(source).as_posix():_digest(p.read_bytes()) for p in sorted(source.rglob('*')) if p.is_file() and p.suffix in {'.py','.json'}}
    plan={'snapshot_id':concrete,'views':frozen,'implementation_digest':_digest(_json_bytes(code))}
    layout=_layout(data_root)
    with writer(layout.root):
        directory=layout.root/'operations'/run_id;_ensure_directory(layout.root,directory)
        path=_safe_path(layout.root,directory/'views.json')
        plan_path=_safe_path(layout.root,directory/'views-plan.json')
        state=json.loads(path.read_bytes()) if path.exists() else {
            'schema_version':'required_views_run.v1','run_id':run_id,'plan':plan,
            'plan_digest':_digest(_json_bytes(plan)),'published_views':{}}
        stored_plan=state.get('plan')
        if plan_path.exists():
            separate_plan=json.loads(plan_path.read_bytes())
            if stored_plan is not None and stored_plan!=separate_plan:
                raise ArtifactError('required View frozen plans disagree')
            stored_plan=separate_plan
        if stored_plan!=plan or state['plan_digest']!=_digest(_json_bytes(plan)):
            raise ArtifactError('required View resume plan or implementation changed')
        # Publish the immutable plan first. A crash before the progress upgrade
        # leaves a readable legacy checkpoint plus the same frozen plan.
        if not plan_path.exists():save_progress(plan_path,plan)
        state.pop('plan',None)
        state['schema_version']='required_views_run.v2'
        state.update(status='RUNNING',stage='REQUIRED_VIEWS',ready_for_consumption=False,failed={})
        save_progress(path,state)
        try:reader=SnapshotReader(layout.root,concrete,validate_sources=True)
        except Exception as exc:
            state.update(status='FAILED',failed={'snapshot':{'error_type':type(exc).__name__,'reason':str(exc)}},
                preflight={k:{'status':'NOT_ASSESSED','reason':'Snapshot admission failed'} for k in frozen})
            save_progress(path,state);return dict(state,plan=plan)
        try:
            from axiom_data.admission_plan import resolve_price_anchors
            resolve_price_anchors(reader, frozen)
        except Exception as exc:
            state['preflight'] = {k:{'status':'NOT_ASSESSED','reason':'anchor preparation failed'} for k in frozen}
            if getattr(exc,'view_label',None) in frozen:
                state['preflight'][exc.view_label] = {'status':'BLOCKED','reason':str(exc)}
            state.update(status='FAILED', failed={getattr(exc, 'view_label', 'price_anchor_preflight'): {
                'stage': 'price_anchor_preflight', 'error_type': type(exc).__name__, 'reason': str(exc)}})
            save_progress(path, state)
            return dict(state, plan=plan)
        from axiom_data.financial_coverage import financial_batch
        with financial_batch(reader):
            from axiom_data.admission_plan import preflight_views
            state['preflight'] = preflight_views(reader, frozen)
            blocked = {k:v for k,v in state['preflight'].items() if v['status']!='READY'}
            if blocked:
                for label in blocked:
                    state['published_views'].pop(label, None)
                state.update(status='FAILED', failed=blocked)
                save_progress(path,state)
                return dict(state,plan=plan)
            candidate_readers = {}
            for group in view_batches(frozen):
                label, spec = group[0]
                state['active_view'] = label
                if len(group) > 1:
                    save_progress(path, state)
                try:
                    pending = []
                    for candidate_label, candidate_spec in group:
                        state['active_view'] = candidate_label
                        old = state['published_views'].get(candidate_label)
                        if old is not None:
                            try:
                                _completed_view(reader,candidate_spec,old,
                                                builders[candidate_spec['kind']],loaders[candidate_spec['kind']],code)
                            except (ArtifactError,OSError,ValueError,KeyError,TypeError):
                                state['published_views'].pop(candidate_label)
                                save_progress(path,state)
                            else:
                                continue
                        if candidate_spec.get('reuse_candidate'):
                            started = time.monotonic()
                            ref = _reuse_view(reader,candidate_spec,builders[candidate_spec['kind']],
                                loaders[candidate_spec['kind']],code,candidate_readers)
                            if ref is not None:
                                state['published_views'][candidate_label] = {'kind':candidate_spec['kind'],
                                    'view_id':ref.view_id,'manifest_digest':ref.manifest_digest}
                                state.setdefault('reused_views',[]).append(candidate_label)
                                state.setdefault('build_seconds',{})[candidate_label]=time.monotonic()-started
                                save_progress(path,state)
                                continue
                        pending.append((candidate_label,candidate_spec))
                    if not pending:
                        continue
                    state['active_view'] = None
                    state['shared_preparation'] = {'kind':spec['kind'], 'labels':[k for k,_ in pending]}
                    context = (adjusted_price_batch(reader, [item['config'] for _, item in pending])
                               if spec['kind'] == 'adjusted_price' and len(pending) > 1 else
                               market_view_batch(reader, [item['config'] for _, item in pending], spec['kind'])
                               if spec['kind'] in {'market_replay', 'market_qlib'} and len(pending) > 1 else
                               financial_view_batch(reader, [item['config'] for _, item in pending])
                               if spec['kind'] == 'pr6_fact' and len(pending) > 1 else
                               event_view_batch(reader, [item['config'] for _, item in pending])
                               if spec['kind'] == 'pr7_fact' and len(pending) > 1 else
                               nullcontext())
                    with context:
                        for label, spec in pending:
                            state['active_view']=label;save_progress(path,state);started=time.monotonic()
                            ref=checked_builders[spec['kind']](reader,**spec['config'])
                            result={'kind':spec['kind'],'view_id':ref.view_id,'manifest_digest':ref.manifest_digest}
                            state['published_views'][label]=result
                            state.setdefault('build_seconds',{})[label]=time.monotonic()-started
                            save_progress(path,state)
                except Exception as exc:
                    state.update(status='FAILED',failed={state['active_view'] or 'shared_preparation':{'error_type':type(exc).__name__, 'reason':str(exc), 'scope':state.get('shared_preparation')}})
                    save_progress(path,state);return dict(state,plan=plan)
        if set(state['published_views'])!=set(frozen):raise ArtifactError('required View closure incomplete')
        state.update(status='VIEWS_BUILT',stage='FULL_ADMISSION');state.pop('active_view',None);state.pop('shared_preparation',None)
        save_progress(path,state);return dict(state,plan=plan)


def _reuse_view(reader, spec, builder, loader, code, candidate_readers):
    """Validate one explicit candidate; dependency changes follow ordinary build."""
    from axiom_data.consumption import SnapshotReader
    from axiom_data.views import publish_rebound_view
    from axiom_data.frozen_execution import executed_code_ref
    from axiom_data.domains import FUNDAMENTAL_DOMAINS, EVENT_DOMAINS
    kind = spec['kind']
    record = spec['reuse_candidate']
    try:
        identity = _identity('view_id', record['view_id'])
        layout = _layout(reader.data_root)
        target = layout.qlib_exports / identity if kind=='market_qlib' else layout.derived_commits(kind) / identity
        manifest = json.loads(_safe_path(layout.root,target/'manifest.json').read_bytes())
        snapshot = manifest['snapshot_ref']['snapshot_id']
        old_reader = candidate_readers.get(snapshot)
        if old_reader is None:
            # A bounded operation-local checked Reader, not a persistent cache.
            candidate_readers.clear()
            old_reader = SnapshotReader(reader.data_root,snapshot)
            candidate_readers[snapshot] = old_reader
        view = _completed_view(old_reader,spec,record,builder,loader,code,return_view=True)
        if view.manifest.get('executed_code_ref') != executed_code_ref():
            return None
        # Historical market formats do not identify executed package bytes.
        if executed_code_ref() is None and kind not in {'pr6_fact','pr7_fact'}:
            return None
        domains = {
            'adjusted_price': {'market_daily','adjustment_factors'},
            'market_replay': {'market_daily','security_status','price_limits','corporate_actions'},
            'market_qlib': {'market_daily'},
            'pr6_fact': set(FUNDAMENTAL_DOMAINS), 'pr7_fact': set(EVENT_DOMAINS),
        }[kind] | {'security_master','trading_calendar'}
        if kind=='market_qlib' and spec['config'].get('adjusted_price_view_id'):
            domains.add('adjustment_factors')
            # Explicit Derived refs are Snapshot-bound; the ordinary builder
            # validates the requested new ref rather than silently substituting.
            return None
        if any(old_reader.snapshot.manifest['domain_refs'][d] != reader.snapshot.manifest['domain_refs'][d]
               for d in domains):
            return None
        old_reader._check_consumed_metadata(force=True)
        reader._check_consumed_metadata(force=True)
        return publish_rebound_view(reader,kind,view)
    except (ArtifactError,OSError,ValueError,KeyError,TypeError):
        return None
