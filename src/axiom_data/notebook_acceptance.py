"""Execute a fixed read-only Notebook smoke against explicit immutable refs."""
import hashlib
import json
import os
from pathlib import Path
from importlib.resources import files

from axiom_data.artifacts import (ArtifactError, _digest, _ensure_directory, _json_bytes,
    _layout, _safe_path, load_snapshot)
from axiom_data.consumption import SnapshotReader, exchange_sessions
from axiom_data.domains.market import _checked_security_identity_state
from axiom_data.gate_a import make_gate_a_plan
from axiom_data.offline_guard import deny_external_data
from axiom_data.recovery import _view_loaders


SMOKE_SOURCE = '''from axiom_data.notebook_acceptance import read_notebook_inputs
import json
result = read_notebook_inputs(**parameters)
print(json.dumps(result, sort_keys=True))'''


def read_notebook_inputs(*, root, snapshot_id, views, target):
    """Public read-only smoke payload, shared by execution and verification."""
    terminal = make_gate_a_plan(target)['terminal_evidence_plan']
    reader = SnapshotReader(root, snapshot_id)
    calendars = exchange_sessions(reader, target['symbols'], target['start_session'], target['end_session'])
    identities = {r['symbol']:r for r in reader.security_master()}
    axis = sorted({d for c in calendars.values() for d in c['sessions']})
    bits = {day:1 << i for i,day in enumerate(axis)}
    expected = {s:sum(bits[d] for d in c['sessions'] if
        _checked_security_identity_state(identities[s],d)=='within_identity_interval') for s,c in calendars.items()}
    coverage = {kind:{s:0 for s in expected} for kind in _view_loaders()}
    summaries = {}
    with deny_external_data(root) as attempts:
        for label, ref in views.items():
            if set(ref) != {'kind','view_id','manifest_digest'} or ref['kind'] not in _view_loaders():
                raise ArtifactError('Notebook explicit View reference required')
            view = _view_loaders()[ref['kind']](root, ref['view_id'], checked_reader=reader)
            if (view.ref.manifest_digest != ref['manifest_digest'] or
                    view.manifest['snapshot_ref']['snapshot_id'] != snapshot_id):
                raise ArtifactError('Notebook View Snapshot mismatch')
            scope = view.manifest['scope']
            if (not set(scope['symbols']) <= set(target['symbols']) or
                    not target['start_session'] <= scope['start_session'] <= scope['end_session'] <= target['end_session']):
                raise ArtifactError('Notebook View outside target')
            mask = sum(bits[d] for d in axis if scope['start_session'] <= d <= scope['end_session'])
            for symbol in scope['symbols']:
                coverage[ref['kind']][symbol] |= expected[symbol] & mask
            summaries[label] = dict(ref, scope=scope)
        if {r['kind'] for r in views.values()} != set(_view_loaders()):
            raise ArtifactError('Notebook requires all five View families')
        if any(mask != expected[s] for covered in coverage.values() for s,mask in covered.items()):
            raise ArtifactError('Notebook View coverage is smaller than the target')
        months = {}
        for day in axis:
            months.setdefault(day[:7], []).append(day)
        market_count = 0; digest = hashlib.sha256()
        for days in months.values():
            for row in reader.market_daily(target['symbols'], days[0], days[-1]):
                market_count += 1
                digest.update(_json_bytes(row))
        if not market_count:
            raise ArtifactError('Notebook target has no market facts')
    return dict(snapshot_id=snapshot_id, snapshot_manifest_digest=reader.snapshot.ref.manifest_digest,
        target_digest=terminal['target_digest'], view_refs=views, views=summaries,
        market_rows=market_count, market_digest='sha256:'+digest.hexdigest(),
        blocked_external_attempts=len(attempts))


def _parameters(root, snapshot_id, views, target):
    return dict(root=str(_layout(root).root.resolve()), snapshot_id=snapshot_id,
                views=json.loads(_json_bytes(views)), target=json.loads(_json_bytes(target)))


def _parameter_cell(parameters):
    return 'import json\nparameters = json.loads(' + repr(json.dumps(parameters, sort_keys=True)) + ')'


def execute_notebook_smoke(root, *, snapshot_id, views, target, output_path):
    """Execute only this packaged smoke; arbitrary Notebook code is not accepted."""
    try:
        import nbformat
        from nbclient import NotebookClient
    except ImportError as exc:
        raise ArtifactError('Notebook smoke requires optional nbformat, nbclient and a Python kernel') from exc
    root = _layout(root).root
    path = Path(output_path)
    path = _safe_path(root, path if path.is_absolute() else root/path)
    if path.suffix != '.ipynb':
        raise ArtifactError('Notebook output must be an in-root .ipynb path')
    if path.exists():
        raise ArtifactError('Notebook output already exists; validate it or choose a new output path')
    parameters = _parameters(root, snapshot_id, views, target)
    expected = read_notebook_inputs(**parameters)
    notebook = nbformat.v4.new_notebook(cells=[
        nbformat.v4.new_code_cell(_parameter_cell(parameters)),
        nbformat.v4.new_code_cell(SMOKE_SOURCE)], metadata={
            'axiom_smoke': {'schema_version':'notebook_smoke.v1','parameters':parameters},
            'kernelspec': {'name':'python3','display_name':'Python 3','language':'python'}})
    environment = dict(os.environ)
    source = str(Path(str(files('axiom_data'))).parent)
    environment['PYTHONPATH'] = source + os.pathsep + environment.get('PYTHONPATH','')
    executed = NotebookClient(notebook, timeout=180, kernel_name='python3').execute(
        cwd=str(root), env=environment)
    _ensure_directory(root, path.parent)
    content = nbformat.writes(executed).encode()
    # Exclusive creation prevents an existing executed artifact being replaced.
    with path.open('xb') as handle:
        handle.write(content)
    report = dict(schema_version='notebook_smoke.v1', snapshot_id=snapshot_id,
        view_refs=views, target_digest=expected['target_digest'],
        executed_notebook_path=path.relative_to(root).as_posix(), content_digest=_digest(content),
        parameters=parameters)
    validate_notebook_smoke(root, report, expected_views=views)
    return report


def validate_notebook_smoke(root, report, *, expected_views):
    """Verify exact packaged cells, actual outputs and re-open their bound refs."""
    root = _layout(root).root
    parameters = report['parameters']
    if (parameters != _parameters(root, report['snapshot_id'], expected_views, parameters['target'])
            or report['view_refs'] != expected_views):
        raise ArtifactError('Notebook parameter binding mismatch')
    content = _safe_path(root, root/report['executed_notebook_path']).read_bytes()
    notebook = json.loads(content)
    if (_digest(content) != report['content_digest'] or
            notebook.get('metadata', {}).get('axiom_smoke') !=
            {'schema_version':'notebook_smoke.v1','parameters':parameters}):
        raise ArtifactError('Notebook artifact binding mismatch')
    cells = notebook['cells']
    sources = [_parameter_cell(parameters), SMOKE_SOURCE]
    if len(cells) != len(sources):
        raise ArtifactError('Notebook fixed smoke cells required')
    for count, (cell, source) in enumerate(zip(cells, sources), 1):
        actual_source = cell.get('source')
        if isinstance(actual_source, list):
            actual_source = ''.join(actual_source)
        if (cell['cell_type'] != 'code' or actual_source != source or cell.get('execution_count') != count
                or any(o.get('output_type') == 'error' for o in cell.get('outputs', []))):
            raise ArtifactError('Notebook execution or fixed source mismatch')
    outputs = cells[-1]['outputs']
    if len(outputs) != 1 or outputs[0].get('output_type') != 'stream' or outputs[0].get('name') != 'stdout':
        raise ArtifactError('Notebook actual read output missing')
    output = outputs[0]['text']
    if isinstance(output, list):
        output = ''.join(output)
    expected = read_notebook_inputs(**parameters)
    if json.loads(output) != expected or report['target_digest'] != expected['target_digest']:
        raise ArtifactError('Notebook output differs from fixed inputs')
    return dict(status='NOTEBOOK_SMOKE_VALIDATED', snapshot_id=report['snapshot_id'],
                target_digest=report['target_digest'], ready_for_consumption=False)
