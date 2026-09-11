"""Offline validation of an explicitly restored immutable artifact closure."""
import fcntl
import inspect
import json

from axiom_data.artifacts import (
    ArtifactError, _digest, _ensure_directory, _identity, _json_bytes,
    _layout, _safe_path, _validated_digest, _load_manifest, rebuild_catalog,
)
from axiom_data.build import _validate_identity
from axiom_data.offline_guard import deny_external_data
from axiom_data.operations import _save
from axiom_data.publication import writer


def _view_loaders():
    from axiom_data.consumption import _load_qlib_view
    from axiom_data.views import _load_adjusted_price_view, _load_market_replay_view
    from axiom_data.pr6_views import _load_pr6_fact_view
    from axiom_data.pr7_views import _load_pr7_fact_view
    return {
        'market_qlib': _load_qlib_view, 'adjusted_price': _load_adjusted_price_view,
        'market_replay': _load_market_replay_view, 'pr6_fact': _load_pr6_fact_view,
        'pr7_fact': _load_pr7_fact_view,
    }


def _require_digest(value):
    _validated_digest('recovery manifest digest', value)


def verify_recovery(data_root, *, run_id, snapshot_id,
                    expected_snapshot_manifest_digest, views, rebuild_views=None):
    """Verify a restored root offline; never accept a baseline or move a pointer.

    Each required View declares kind, view_id and manifest_digest. Optional
    rebuild requests use materialize_views' existing kind/config contract and
    must reproduce those exact declared artifacts. Resume always revalidates.
    """
    from axiom_data import SnapshotReader, materialize_views

    concrete = _validate_identity('snapshot_id', snapshot_id)
    _identity('run_id', run_id)
    _require_digest(expected_snapshot_manifest_digest)
    loaders = _view_loaders()
    if not isinstance(views, dict) or not views:
        raise ArtifactError('recovery requires nonempty explicit View references')
    for label, spec in views.items():
        _identity('view_label', label)
        if (not isinstance(spec, dict)
                or set(spec) != {'kind', 'view_id', 'manifest_digest'}
                or not isinstance(spec['kind'], str) or spec['kind'] not in loaders):
            raise ArtifactError('invalid recovery View reference')
        _validate_identity('view_id', spec['view_id'])
        _require_digest(spec['manifest_digest'])
    if rebuild_views is not None:
        if not isinstance(rebuild_views, dict) or not rebuild_views or not set(rebuild_views) <= set(views):
            raise ArtifactError('rebuild requests must name required recovery Views')
        for label, spec in rebuild_views.items():
            if (not isinstance(spec, dict) or set(spec) != {'kind', 'config'}
                    or not isinstance(spec['config'], dict)
                    or spec.get('kind') != views[label]['kind']):
                raise ArtifactError('recovery rebuild kind differs from required View')
            if spec['kind'] not in {'market_qlib', 'market_replay', 'adjusted_price'}:
                raise ArtifactError('exact recovery rebuild requires a creation-time-pinnable View kind')
        from axiom_data import build_adjusted_price_view, build_market_replay_view, build_qlib_view
        builders = dict(adjusted_price=build_adjusted_price_view,
                        market_replay=build_market_replay_view, market_qlib=build_qlib_view)
        for spec in rebuild_views.values():
            try:
                inspect.signature(builders[spec['kind']]).bind(data_root, concrete, **spec['config'])
            except TypeError as exc:
                raise ArtifactError('invalid recovery rebuild arguments') from exc
    plan = json.loads(_json_bytes({
        'snapshot_id': concrete,
        'expected_snapshot_manifest_digest': expected_snapshot_manifest_digest,
        'views': views, 'rebuild_views': rebuild_views,
    }))
    layout = _layout(data_root)
    with writer(layout.root):
        directory = layout.root / 'operations' / run_id
        _ensure_directory(layout.root, directory)
    path = _safe_path(layout.root, directory / 'recovery.json')
    lock_path = _safe_path(layout.root, directory / 'recovery.lock')
    with lock_path.open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ArtifactError('recovery run already active') from exc
        if path.exists():
            old = json.loads(path.read_bytes())
            if old.get('plan') != plan or old.get('plan_digest') != _digest(_json_bytes(plan)):
                raise ArtifactError('recovery resume plan changed')
        state = {
            'schema_version': 'recovery_run.v1', 'run_id': run_id,
            'data_root': str(layout.root),
            'plan': plan, 'plan_digest': _digest(_json_bytes(plan)),
            'status': 'RUNNING', 'stage': 'SNAPSHOT_VALIDATION',
            'ready_for_consumption': False,
        }
        _save(path, state)
        attempts = []
        try:
            with deny_external_data(layout.root) as attempts:
                reader = SnapshotReader(layout.root, concrete)
                snapshot = reader.snapshot
                if snapshot.ref.manifest_digest != expected_snapshot_manifest_digest:
                    raise ArtifactError('restored Snapshot manifest differs from frozen reference')
                if plan['rebuild_views'] is not None:
                    state['stage'] = 'REQUIRED_VIEW_REBUILD'
                    _save(path, state)
                    rebuilt = materialize_views(
                        layout.root, run_id=run_id + '-views', snapshot_id=concrete,
                        views=plan['rebuild_views'],
                    )
                    if rebuilt['status'] != 'VIEWS_BUILT':
                        raise ArtifactError('required recovery View rebuild failed')
                    for label, result in rebuilt['published_views'].items():
                        if result != plan['views'][label]:
                            raise ArtifactError('rebuilt View differs from frozen reference')
                state['stage'] = 'REQUIRED_VIEW_VALIDATION'
                _save(path, state)
                validated = {}
                for label, spec in plan['views'].items():
                    state['active_view'] = label
                    _save(path, state)
                    checked_reader = reader
                    if spec['kind'] == 'pr6_fact':
                        manifest, _ = _load_manifest(
                            layout.root, layout.derived_commits('pr6_fact') / spec['view_id'],
                            artifact_type='pr6_fact_view',
                            schema_version=('pr6_fact_view.v1', 'pr6_fact_view.v2'),
                            identity_field='view_id', identity=spec['view_id'],
                        )
                        if manifest['schema_version'] == 'pr6_fact_view.v1':
                            checked_reader = None
                    view = loaders[spec['kind']](
                        layout.root, spec['view_id'], checked_reader=checked_reader)
                    if (view.ref.manifest_digest != spec['manifest_digest']
                            or view.manifest['snapshot_ref']['snapshot_id'] != concrete):
                        raise ArtifactError('restored View differs from frozen Snapshot/View reference')
                    validated[label] = dict(spec, schema_version=view.manifest['schema_version'])
                state.pop('active_view', None)
                state['stage'] = 'CATALOG_REBUILD'
                _save(path, state)
                with writer(layout.root):
                    catalog_entries = rebuild_catalog(layout.root)
                if attempts:
                    raise ArtifactError('recovery attempted network or external legacy data access')
                state.update(status='RECOVERY_VALIDATED', stage='RECOVERY_VALIDATED',
                             catalog_entries=catalog_entries, validated_views=validated,
                             snapshot_manifest_digest=snapshot.ref.manifest_digest)
        except Exception as exc:
            state.update(status='FAILED', error_type=type(exc).__name__)
        state['blocked_external_attempts'] = len(attempts)
        _save(path, state)
        return state
