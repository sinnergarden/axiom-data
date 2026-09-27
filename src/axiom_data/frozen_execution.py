"""Execute built-in builds from a content-addressed copy of their package.

This boundary transports JSON inputs and small public result references, never
live supplier clients. Collection stays in the caller; build execution starts
only after its explicit Raw/Snapshot references and configuration are frozen.
"""
from __future__ import annotations

import builtins
from contextvars import ContextVar
from contextlib import nullcontext
from dataclasses import asdict, fields, is_dataclass
from functools import wraps
import importlib
import inspect
import json
from pathlib import Path, PurePosixPath
import platform
import subprocess
import sys
import tempfile

from axiom_data.artifacts import (
    ArtifactError, _digest, _identity, _json_bytes, _layout,
    _publish_directory, _safe_path, _write_file,
)

_active = ContextVar('axiom_frozen_execution', default=None)
_code_cache = ContextVar('axiom_frozen_code_cache', default=None)


def executed_code_ref():
    """The executing package identity, independent of a run's timestamp/inputs."""
    return _active.get()


def is_frozen():
    return _active.get() is not None


def _runtime():
    return {'implementation': platform.python_implementation(), 'version': platform.python_version(),
            'platform': sys.platform, 'machine': platform.machine()}


def _package_files(source):
    for path in sorted(source.rglob('*')):
        if (path.is_file() and path.suffix in {'.py', '.json', '.zip'}
                and not any(part.startswith('.') for part in path.relative_to(source).parts)
                and path.name.lower() not in {'credentials.json', 'secrets.json', 'tokens.json'}):
            if path.is_symlink():
                raise ArtifactError('frozen source must not contain symlinks')
            yield path


def capture_code(data_root):
    source = Path(__file__).parent
    project = source.parent.parent
    contents = {'axiom_data/' + path.relative_to(source).as_posix(): path.read_bytes()
                for path in _package_files(source)}
    for name in ('pyproject.toml', 'uv.lock', 'poetry.lock', 'requirements.txt'):
        path = project / name
        if path.is_file():
            contents[name] = path.read_bytes()
    manifest = {'schema_version': 'executed_code.v1', 'runtime': _runtime(),
                'files': {name: _digest(value) for name, value in contents.items()}}
    identity = 'code-' + _digest(_json_bytes(manifest)).split(':')[1]
    ref = {'schema_version': 'executed_code.v1', 'bundle_id': identity,
           'manifest_digest': _digest(_json_bytes(manifest))}
    layout = _layout(data_root)
    target = layout.root / 'code_bundles' / identity
    if target.exists():
        validate_code(data_root, ref)
        return ref
    def prepare(candidate):
        _write_file(candidate / 'bundle.json', _json_bytes(manifest))
        for name, content in contents.items():
            path = candidate / name
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_file(path, content)
    _publish_directory(layout, target, prepare)
    from axiom_data.publication import seal
    seal(target)
    validate_code(data_root, ref)
    return ref


def validate_code(data_root, ref):
    if (not isinstance(ref, dict) or set(ref) != {'schema_version', 'bundle_id', 'manifest_digest'}
            or ref['schema_version'] != 'executed_code.v1'):
        raise ArtifactError('invalid executed code reference')
    identity = _identity('bundle_id', ref['bundle_id'])
    cache = _code_cache.get()
    key = ('validated', str(Path(data_root).absolute()), _json_bytes(ref))
    if cache is not None and key in cache:
        return cache[key]
    root = Path(data_root)
    target = _safe_path(root, root / 'code_bundles' / identity)
    content = _safe_path(root, target / 'bundle.json').read_bytes()
    if (_digest(content) != ref['manifest_digest'] or
            identity != 'code-' + ref['manifest_digest'].removeprefix('sha256:')):
        raise ArtifactError('executed code bundle digest mismatch')
    manifest = json.loads(content)
    if (manifest.get('schema_version') != 'executed_code.v1'
            or not isinstance(manifest.get('files'), dict)
            or not {'axiom_data/__init__.py', 'axiom_data/frozen_execution.py'} <= set(manifest['files'])):
        raise ArtifactError('invalid executed code manifest')
    for name, digest in manifest['files'].items():
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or str(path) != name:
            raise ArtifactError('invalid executed code member')
        if _digest(_safe_path(root, target / name).read_bytes()) != digest:
            raise ArtifactError('executed code payload mismatch: ' + name)
    actual = {p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()}
    if actual != set(manifest['files']) | {'bundle.json'}:
        raise ArtifactError('unexpected executed code bundle member')
    if cache is not None:
        cache[key] = (target, manifest)
    return target, manifest


def _json_inputs(value):
    if isinstance(value, Path):
        return str(value.absolute())
    if isinstance(value, dict):
        return {key: _json_inputs(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_inputs(item) for item in value]
    return value


def _source_audit():
    """Git is optional audit context; actual captured bytes remain authoritative."""
    project = Path(__file__).parent.parent.parent
    if not (project / '.git').exists():
        return {'git': 'unavailable'}
    paths = [str(path.relative_to(project)) for path in _package_files(Path(__file__).parent)]
    paths += ['pyproject.toml', 'uv.lock', 'poetry.lock', 'requirements.txt']
    try:
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=project,
                              capture_output=True, text=True, check=True).stdout.strip()
        diff = subprocess.run(['git', 'diff', '--no-ext-diff', '--no-textconv', 'HEAD', '--', *paths],
                              cwd=project, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return {'git': 'unavailable'}
    return {'git_head': head, 'dirty_diff': diff}


def _freeze_invocation(root, invocation, *, run_id=None, stage=None):
    """An existing explicit run keeps its original code, even after checkout edits."""
    layout = _layout(root)
    target = None
    if run_id is not None:
        _identity('run_id', run_id)
        target = _safe_path(layout.root, layout.root / 'operations' / run_id / ('frozen-' + stage))
        if target.exists():
            envelope = json.loads(_safe_path(layout.root, target / 'invocation.json').read_bytes())
            if envelope.get('schema_version') != 'frozen_invocation.v1':
                raise ArtifactError('unsupported frozen invocation protocol')
            stored = json.loads(_json_bytes(envelope.get('invocation')))
            # A restored root changes the storage location, not the frozen refs.
            location = stored.get('kwargs', stored)
            location['data_root'] = str(layout.root.absolute())
            from axiom_data.deprecated.view_protocols import equivalent_invocation
            if not equivalent_invocation(stored, invocation):
                raise ArtifactError('frozen execution resume plan inputs changed; use a distinct run_id')
            return dict(envelope, invocation=stored)
    envelope = {'schema_version': 'frozen_invocation.v1', 'invocation': invocation,
                'code': capture_code(root)}
    # Audit metadata never changes the stable code or artifact identity.
    audit = _source_audit()
    if target is None:
        call_id = 'call-' + _digest(_json_bytes(envelope)).removeprefix('sha256:')
        target = layout.root / 'operations' / 'frozen_calls' / call_id
    if target.exists():
        stored = json.loads(_safe_path(layout.root, target / 'invocation.json').read_bytes())
        if stored != envelope:
            raise ArtifactError('frozen invocation publication conflict')
        return envelope
    if target is not None:
        def prepare(candidate):
            _write_file(candidate / 'invocation.json', _json_bytes(envelope))
            _write_file(candidate / 'source-audit.json', _json_bytes(audit))
        _publish_directory(layout, target, prepare)
        from axiom_data.publication import seal
        seal(target)
    return envelope


def execute(data_root, invocation, *, run_id=None, stage=None):
    root = Path(data_root).absolute()
    invocation = json.loads(_json_bytes(_json_inputs(invocation)))
    envelope = _freeze_invocation(root, invocation, run_id=run_id, stage=stage)
    target, manifest = validate_code(root, envelope['code'])
    if manifest['runtime'] != _runtime():
        raise ArtifactError('frozen execution runtime differs from captured runtime')
    # -I ignores PYTHONPATH and the current checkout; only the captured package
    # is inserted, with the existing standard-library/dependency environment.
    bootstrap = ('import sys; sys.dont_write_bytecode=True; sys.path.insert(0,sys.argv[1]); '
                 'from axiom_data.frozen_execution import _child; _child(sys.argv[2],sys.argv[3])')
    from axiom_data.offline_guard import active_offline_guard
    offline = active_offline_guard.get()
    envelope = dict(envelope, offline_root=str(offline[0]) if offline else None)
    with tempfile.TemporaryDirectory(prefix='frozen-call-') as temp:
        request = Path(temp) / 'request.json'
        response = Path(temp) / 'response.json'
        request.write_bytes(_json_bytes(envelope))
        process = subprocess.run([sys.executable, '-I', '-c', bootstrap, str(target),
                                  str(request), str(response)], cwd=target, capture_output=True, text=True)
        if not response.exists():
            raise ArtifactError('frozen execution failed: ' + process.stderr[-2000:])
        result = json.loads(response.read_bytes())
    if offline is not None:
        offline[1].extend(result.get('blocked_external_attempts', []))
    if 'error' in result:
        error = result['error']
        module = error['module']
        cls = (getattr(builtins, error['name'], None) if module == 'builtins' else
               getattr(importlib.import_module(module), error['name'], None)
               if module.startswith('axiom_data.') else None)
        if isinstance(cls, type) and issubclass(cls, BaseException):
            raise cls(error['message'])
        raise ArtifactError(error['name'] + ': ' + error['message'])
    value = result['value']
    if 'result_type' in result:
        module, name = result['result_type'].rsplit('.', 1)
        cls = getattr(importlib.import_module(module), name)
        if not is_dataclass(cls) or set(value) != {field.name for field in fields(cls)}:
            raise ArtifactError('invalid frozen result reference')
        # Validation occurred against the captured contracts in the child. A
        # second constructor call here could reread a now-edited checkout.
        reference = object.__new__(cls)
        for key, item in value.items():
            object.__setattr__(reference, key, item)
        return reference
    return value


def frozen_operation(stage=None):
    """One subprocess for a public operation, including every nested builder."""
    def decorate(function):
        @wraps(function)
        def wrapped(data_root, *args, **kwargs):
            if is_frozen():
                return function(data_root, *args, **kwargs)
            bound = inspect.signature(function).bind(data_root, *args, **kwargs)
            bound.apply_defaults()
            inputs = dict(bound.arguments)
            inputs['data_root'] = str(Path(data_root).absolute())
            if stage is not None:
                _identity('run_id', inputs['run_id'])
                from axiom_data.build import _validate_identity
                if stage == 'views':
                    _validate_identity('snapshot_id', inputs['snapshot_id'])
                    if not isinstance(inputs.get('views'), dict) or not inputs['views']:
                        raise ArtifactError('explicit nonempty required View plan required')
                elif stage == 'canonical':
                    # Reject invalid new requests before publication. A persisted
                    # run must validate its contracts inside its original package.
                    root = Path(inputs['data_root'])
                    frozen = root / 'operations' / inputs['run_id'] / 'frozen-canonical'
                    if inputs.get('parent_snapshot_id') == 'current':
                        if frozen.exists():
                            record = json.loads(_safe_path(root, frozen / 'invocation.json').read_bytes())
                            inputs['parent_snapshot_id'] = record['invocation']['kwargs']['parent_snapshot_id']
                        else:
                            from axiom_data.operations import _resolve_snapshot_id
                            inputs['parent_snapshot_id'] = _resolve_snapshot_id(root, 'current')
                    if not frozen.exists():
                        from axiom_data.operations import _validate_domain_inputs
                        _validate_domain_inputs(inputs['domain_inputs'])
            if stage == 'views' and isinstance(inputs.get('views'), dict):
                from axiom_data.views import _stored_view_kind
                inputs['views'] = json.loads(_json_bytes(inputs['views']))
                for spec in inputs['views'].values():
                    if isinstance(spec, dict) and isinstance(spec.get('kind'), str):
                        spec['kind'] = _stored_view_kind(spec['kind'])
            invocation = {'function': function.__module__ + '.' + function.__name__, 'kwargs': inputs}
            return execute(data_root, invocation, run_id=inputs.get('run_id') if stage else None, stage=stage)
        return wrapped
    return decorate


def execute_builder(builder, request, *, application=False):
    """Transport declared built-in executor inputs; custom protocols stay local."""
    if not type(builder).__module__.startswith('axiom_data.'):
        raise ArtifactError('custom persistence builder needs an explicit execution boundary')
    options = dict(builder_config=builder.builder_config, created_at=builder.created_at)
    parameters = inspect.signature(type(builder)).parameters
    candidates = {'commit_id': builder.expected_commit_id,
                  'calendar_commit_id': builder.calendar_commit_id,
                  'security_master_commit_id': builder.security_master_commit_id,
                  'dependency_commit_ids': builder.dependency_commit_ids}
    for name, value in candidates.items():
        if name in parameters or any(p.kind == p.VAR_KEYWORD for p in parameters.values()):
            options[name] = value
    return execute(builder.layout.root, {'builder': type(builder).__module__ + '.' + type(builder).__name__,
        'data_root': str(builder.layout.root.absolute()), 'domain': builder.domain,
        'options': options, 'request': asdict(request), 'application': application})


def _child(request_path, response_path):
    envelope = json.loads(Path(request_path).read_bytes())
    token = _active.set(envelope['code'])
    cache_token = _code_cache.set({})
    from axiom_data.offline_guard import deny_external_data
    guard = deny_external_data(envelope['offline_root']) if envelope.get('offline_root') else nullcontext([])
    attempts = []
    try:
        with guard as attempts:
            invocation = envelope['invocation']
            if 'builder' in invocation:
                from axiom_data.deprecated.resources import builder_implementation
                module, name = builder_implementation(invocation['builder']).rsplit('.', 1)
                cls = getattr(importlib.import_module(module), name)
                from axiom_data.build import BuildApplication, BuildRequest
                builder = cls(invocation['data_root'], invocation['domain'], **invocation['options'])
                value = (BuildApplication(invocation['domain'], builder).build(**invocation['request'])
                         if invocation['application'] else builder(BuildRequest(**invocation['request'])))
            else:
                module, name = invocation['function'].rsplit('.', 1)
                value = getattr(importlib.import_module(module), name)(**invocation['kwargs'])
            if attempts:
                raise ArtifactError('frozen execution attempted external data access')
            result = {'value': asdict(value) if is_dataclass(value) else value}
            if is_dataclass(value):
                result['result_type'] = type(value).__module__ + '.' + type(value).__name__
    except BaseException as exc:
        result = {'error': {'module': type(exc).__module__, 'name': type(exc).__name__, 'message': str(exc)}}
    finally:
        _active.reset(token)
        _code_cache.reset(cache_token)
    result['blocked_external_attempts'] = attempts
    Path(response_path).write_bytes(_json_bytes(result))


from axiom_data.deprecated.view_protocols import EXECUTION_SCHEMAS as HISTORICAL_EXECUTION_SCHEMAS

VIEW_EXECUTION_SCHEMAS = HISTORICAL_EXECUTION_SCHEMAS | {
    'adjusted_price_view.v2', 'market_replay_view.v2', 'qlib_view.v3', 'qlib_view.v4',
    'financial_fact_view.v6', 'event_fact_view.v5',
}


def bind_view_execution(manifest, schema_version):
    ref = executed_code_ref()
    if ref is not None:
        manifest.update(schema_version=schema_version, executed_code_ref=ref)


def source_bundle(data_root, ref):
    """Historical View text representation, resolved from one shared package."""
    target, manifest = validate_code(data_root, ref)
    cache = _code_cache.get()
    key = ('source', str(target))
    if cache is not None and key in cache:
        return cache[key]
    prefix = 'axiom_data/'
    bundle = {name[len(prefix):]: (target / name).read_text()
              for name in manifest['files']
              if name.startswith(prefix) and Path(name).suffix in {'.py', '.json'}}
    if cache is not None:
        cache[key] = bundle
    return bundle


def view_source_bundle(data_root, manifest, payload):
    value = json.loads(payload)
    if manifest['schema_version'] in VIEW_EXECUTION_SCHEMAS:
        if value != manifest.get('executed_code_ref'):
            raise ArtifactError('View source reference differs from executed code')
        return source_bundle(data_root, value)
    return value
