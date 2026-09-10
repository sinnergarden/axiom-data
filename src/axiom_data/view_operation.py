"""Resumable required-View stage over the existing immutable public builders."""
import inspect
import json
import time
from importlib.resources import files

from axiom_data.artifacts import ArtifactError, _identity, _layout, _ensure_directory, _safe_path, _digest, _json_bytes
from axiom_data.build import _validate_identity
from axiom_data.operations import _save
from axiom_data.publication import writer


def materialize_views(data_root, *, run_id, snapshot_id, views):
    """Build frozen View requests; required failures keep the candidate unready.

    Resume replays the existing builders and validates their published results.
    The execution record never substitutes for artifact identity or validation.
    """
    from axiom_data.views import build_adjusted_price_view, build_market_replay_view
    from axiom_data.consumption import build_qlib_view
    from axiom_data.pr6_views import build_pr6_fact_view
    from axiom_data.pr7_views import build_pr7_fact_view
    from axiom_data.views import _build_adjusted_price_view, _build_market_replay_view
    from axiom_data.consumption import SnapshotReader, _build_qlib_view
    from axiom_data.pr6_views import _build_pr6_fact_view
    from axiom_data.pr7_views import _build_pr7_fact_view
    builders={'adjusted_price':build_adjusted_price_view,'market_replay':build_market_replay_view,
              'market_qlib':build_qlib_view,'pr6_fact':build_pr6_fact_view,'pr7_fact':build_pr7_fact_view}
    checked_builders={'adjusted_price':_build_adjusted_price_view,'market_replay':_build_market_replay_view,
        'market_qlib':_build_qlib_view,'pr6_fact':_build_pr6_fact_view,'pr7_fact':_build_pr7_fact_view}
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
        state=json.loads(path.read_bytes()) if path.exists() else {
            'schema_version':'required_views_run.v1','run_id':run_id,'plan':plan,
            'plan_digest':_digest(_json_bytes(plan)),'published_views':{}}
        if state['plan']!=plan or state['plan_digest']!=_digest(_json_bytes(plan)):
            raise ArtifactError('required View resume plan or implementation changed')
        state.update(status='RUNNING',stage='REQUIRED_VIEWS',ready_for_consumption=False,failed={})
        _save(path,state)
        try:reader=SnapshotReader(layout.root,concrete)
        except Exception as exc:
            state.update(status='FAILED',failed={'snapshot':{'error_type':type(exc).__name__}})
            _save(path,state);return state
        for label,spec in frozen.items():
            state['active_view']=label;_save(path,state);started=time.monotonic()
            try:
                ref=checked_builders[spec['kind']](reader,**spec['config'])
                result={'kind':spec['kind'],'view_id':ref.view_id,'manifest_digest':ref.manifest_digest}
                old=state['published_views'].get(label)
                if old is not None and old!=result:raise ArtifactError('required View resume result differs from frozen request')
                state['published_views'][label]=result
                state.setdefault('build_seconds',{})[label]=time.monotonic()-started
            except Exception as exc:
                state.update(status='FAILED',failed={label:{'error_type':type(exc).__name__}})
                _save(path,state);return state
            _save(path,state)
        if set(state['published_views'])!=set(frozen):raise ArtifactError('required View closure incomplete')
        state.update(status='VIEWS_BUILT',stage='FULL_ADMISSION');state.pop('active_view',None)
        _save(path,state);return state
