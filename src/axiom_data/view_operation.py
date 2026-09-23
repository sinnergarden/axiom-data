"""Resumable required-View stage over the existing immutable public builders."""
import inspect
import json
import time
from importlib.resources import files

from axiom_data.artifacts import ArtifactError, _identity, _layout, _ensure_directory, _safe_path, _digest, _json_bytes
from axiom_data.build import _validate_identity
from axiom_data.operations import save_progress
from axiom_data.publication import writer


def _completed_view(reader, spec, record, builder, loader, code):
    """Validate the artifact itself and bind its declared inputs to this request."""
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
        _validate_qlib_inputs(reader, **{k:v for k,v in config.items() if k not in {'data_root','snapshot_id'}})
        actual_basis = ('unadjusted' if manifest['schema_version'] == 'qlib_view.v1'
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
    return view.ref


def materialize_views(data_root, *, run_id, snapshot_id, views):
    """Build frozen View requests; required failures keep the candidate unready.

    Resume validates completed artifacts before entering a builder.
    The execution record never substitutes for artifact identity or validation.
    """
    from axiom_data.views import build_adjusted_price_view, build_market_replay_view
    from axiom_data.consumption import build_qlib_view
    from axiom_data.financial_views import build_financial_fact_view
    from axiom_data.event_views import build_event_fact_view
    from axiom_data.views import _build_adjusted_price_view, _build_market_replay_view
    from axiom_data.consumption import SnapshotReader, _build_qlib_view
    from axiom_data.financial_views import build_financial_fact_view_from_reader
    from axiom_data.event_views import build_event_fact_view_from_reader
    from axiom_data.views import _load_adjusted_price_view, _load_market_replay_view
    from axiom_data.consumption import _load_qlib_view
    from axiom_data.financial_views import load_financial_fact_view_with_reader
    from axiom_data.event_views import load_event_fact_view_with_reader
    loaders={'adjusted_price':_load_adjusted_price_view,'market_replay':_load_market_replay_view,
        'market_qlib':_load_qlib_view,'pr6_fact':load_financial_fact_view_with_reader,'pr7_fact':load_event_fact_view_with_reader}
    builders={'adjusted_price':build_adjusted_price_view,'market_replay':build_market_replay_view,
              'market_qlib':build_qlib_view,'pr6_fact':build_financial_fact_view,'pr7_fact':build_event_fact_view}
    checked_builders={'adjusted_price':_build_adjusted_price_view,'market_replay':_build_market_replay_view,
        'market_qlib':_build_qlib_view,'pr6_fact':build_financial_fact_view_from_reader,'pr7_fact':build_event_fact_view_from_reader}
    concrete=_validate_identity('snapshot_id',snapshot_id);_identity('run_id',run_id)
    if not isinstance(views,dict) or not views:raise ArtifactError('explicit nonempty required View plan required')
    frozen=json.loads(_json_bytes(views))
    for label,spec in frozen.items():
        _identity('view_label',label)
        if not isinstance(spec,dict) or set(spec)!={'kind','config'} or not isinstance(spec['kind'],str) or spec['kind'] not in builders or not isinstance(spec['config'],dict):
            raise ArtifactError('required View needs a registered kind and config')
        try:inspect.signature(builders[spec['kind']]).bind(data_root,concrete,**spec['config'])
        except TypeError as exc:raise ArtifactError('invalid required View arguments') from exc
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
        try:reader=SnapshotReader(layout.root,concrete)
        except Exception as exc:
            state.update(status='FAILED',failed={'snapshot':{'error_type':type(exc).__name__}})
            save_progress(path,state);return dict(state,plan=plan)
        from axiom_data.financial_coverage import financial_batch
        with financial_batch(reader):
            for label,spec in frozen.items():
                state['active_view']=label;save_progress(path,state);started=time.monotonic()
                try:
                    old=state['published_views'].get(label)
                    if old is not None:
                        try:
                            _completed_view(reader,spec,old,builders[spec['kind']],loaders[spec['kind']],code)
                        except (ArtifactError,OSError,ValueError,KeyError,TypeError):
                            state['published_views'].pop(label)
                            save_progress(path,state)
                        else:
                            continue
                    ref=checked_builders[spec['kind']](reader,**spec['config'])
                    result={'kind':spec['kind'],'view_id':ref.view_id,'manifest_digest':ref.manifest_digest}
                    state['published_views'][label]=result
                    state.setdefault('build_seconds',{})[label]=time.monotonic()-started
                except Exception as exc:
                    state.update(status='FAILED',failed={label:{'error_type':type(exc).__name__}})
                    save_progress(path,state);return dict(state,plan=plan)
                save_progress(path,state)
        if set(state['published_views'])!=set(frozen):raise ArtifactError('required View closure incomplete')
        state.update(status='VIEWS_BUILT',stage='FULL_ADMISSION');state.pop('active_view',None)
        save_progress(path,state);return dict(state,plan=plan)
