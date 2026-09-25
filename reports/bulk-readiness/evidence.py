"""REPRODUCTION_EVIDENCE: bounded source census and isolated diagnostic probe.

Reads only security-master Raw payloads, never recollects or publishes data.
"""
import ast
import csv
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from axiom_data import ArtifactError, materialize_views
from axiom_data.exchange_security import parse_termination


def main(root, output):
    root, output = Path(root), Path(output)
    assert not output.resolve().is_relative_to(root.resolve())
    summary = json.loads((output/'summary.json').read_text())
    snapshot = json.loads((root/'snapshots'/summary['snapshot_id']/'manifest.json').read_text())
    master = snapshot['domain_refs']['security_master']['domain_commit_id']
    manifest = json.loads((root/'canonical/security_master/commits'/master/'manifest.json').read_text())
    official, supplier = {}, {}
    raw_bytes = 0
    for ref in manifest['ordered_raw_batch_refs']:
        path = root/'raw/batches'/ref['raw_batch_id']
        raw = json.loads((path/'manifest.json').read_text())
        entry, = raw['payload_files']
        payload = (path/entry['path']).read_bytes()
        raw_bytes += len(payload)
        assert 'sha256:'+hashlib.sha256(payload).hexdigest() == entry['content_digest']
        if raw['source_profile_version'] == 'exchange_security.v1':
            official.update(parse_termination(raw['request']['exchange'], payload))
        else:
            for row in json.loads(payload):
                assert row['ts_code'] not in supplier
                supplier[row['ts_code']] = row
    def day(value):
        return value[:4]+'-'+value[4:6]+'-'+value[6:] if value else None
    differences = []
    contradictions = []
    for symbol in manifest['builder_config']['symbols']:
        row = supplier[symbol]
        boundary = official.get(symbol)
        if row['list_status'] == 'D':
            if boundary is None or boundary['list_session'] != day(row['list_date']):
                contradictions.append(symbol)
            elif boundary['delist_session'] != day(row.get('delist_date')):
                differences.append(dict(symbol=symbol, supplier=day(row.get('delist_date')),
                    official=boundary['delist_session'], resolution='exchange_security.v1 official exclusive identity end'))
        elif boundary or row.get('delist_date'):
            contradictions.append(symbol)
    source = dict(raw_payload_bytes=raw_bytes, raw_batches=len(manifest['ordered_raw_batch_refs']),
        scoped_securities=len(manifest['builder_config']['symbols']),
        termination_date_differences=differences, unresolved_identity_contradictions=contradictions)
    (output/'source-authority.json').write_text(json.dumps(source,indent=2,sort_keys=True)+'\n')

    @contextmanager
    def bad_batch(reader, configs):
        raise ArtifactError('probe: first member has an unavailable input')
        yield

    plans = {label:dict(kind='adjusted_price', config=dict(symbols=[symbol],
        start_session='2025-01-02',end_session='2025-01-03',anchor_session='2025-01-03',
        pit_policy='research_non_pit',decision_cutoff='2025-01-03'))
        for label,symbol in [('first','000001.SZ'),('last','000002.SZ')]}
    with tempfile.TemporaryDirectory() as directory:
        with patch('axiom_data.consumption.SnapshotReader', return_value=SimpleNamespace()), \
             patch('axiom_data.views.adjusted_price_batch', bad_batch):
            result = materialize_views(directory,run_id='batch-attribution-probe',
                snapshot_id=summary['snapshot_id'],views=plans)
        assert result['status'] == 'FAILED'
        assert result['failed'] == {'last': {'error_type':'ArtifactError'}}
        assert not result['published_views']
    (output/'batch-attribution.json').write_text(json.dumps(dict(
        status='REPRODUCED', injected_failure='first member during shared preparation',
        recorded_failure=result['failed'], published_views=result['published_views'],
        limitation='Isolated operation-control probe, not a real source failure.'),indent=2)+'\n')
    sources = Path(__file__).resolve().parents[2]/'src/axiom_data'
    # Search inventory, not an assertion that every site is reachable by this plan.
    with (output/'failure-sites.csv').open('w', newline='') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(['file', 'function', 'line', 'condition', 'failure'])
        def walk(node, path, function='', conditions=()):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = node.name
            if isinstance(node, ast.If):
                test = ast.unparse(node.test)
                for child in node.body:
                    walk(child, path, function, conditions+(test,))
                for child in node.orelse:
                    walk(child, path, function, conditions+('not ('+test+')',))
                return
            if isinstance(node, (ast.Raise, ast.Assert)):
                writer.writerow([path, function, node.lineno, ' AND '.join(conditions), ast.unparse(node)])
            for child in ast.iter_child_nodes(node):
                walk(child, path, function, conditions)
        for path in sorted(sources.rglob('*.py')):
            walk(ast.parse(path.read_text()), str(path.relative_to(sources)))
    dates = []
    for path in sorted((sources/'contracts').glob('*.json')):
        contract = json.loads(path.read_text())
        for field in contract.get('fields', []):
            if isinstance(field, dict) and any(word in str(field).lower() for word in ('date', 'session', 'timestamp', 'effective_', 'available_', 'observed_')):
                dates.append(dict(contract=path.name, field=field))
    (output/'date-fields.json').write_text(json.dumps(dates, indent=2, sort_keys=True)+'\n')
    print(json.dumps(dict(source_disagreements=len(differences),unresolved=len(contradictions),
                         raw_payload_bytes=raw_bytes,batch_attribution='REPRODUCED')))


if __name__ == '__main__':
    main(*sys.argv[1:])
