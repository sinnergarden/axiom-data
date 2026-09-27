"""REPRODUCTION_EVIDENCE: independently check the completed small benchmark."""
import json
from importlib.resources import files
from pathlib import Path
import sys

from axiom_data.artifacts import _digest, _load_manifest, _safe_path, _validate_manifest_identity


def check(before_path, after_path):
    before = json.loads(before_path.read_bytes())
    after = json.loads(after_path.read_bytes())
    assert before['status'] == after['status'] == 'PASS'
    assert before['snapshot'] == after['snapshot']
    assert before['configs'] == after['configs'] and len(after['views']) == 50
    assert before['views'] == after['views']
    assert before['rejections'] == after['equivalent_rejections']
    assert {'000005.SZ', '000016.SZ'} <= {r['config']['symbols'][0] for r in before['rejections']}
    assert set(before['milestones']) == set(after['milestones']) == {'1', '10', '50'}
    assert after['serial_batch_identity_equal']
    assert after['phases']['public_batch_build']['builder_calls'] == 50
    assert after['phases']['completed_resume']['builder_calls'] == 0
    root = after_path.parent / 'outputs'
    source = files('axiom_data')
    code = {p.relative_to(source).as_posix():_digest(p.read_bytes()) for p in source.rglob('*')
            if p.is_file() and p.suffix in {'.py', '.json'}}
    count = 0
    for index, expected in enumerate(before['views']):
        ref = after['published_views'][str(index)]
        target = root / 'derived/pr6_fact/commits' / ref['view_id']
        manifest, digest = _load_manifest(root, target, artifact_type='pr6_fact_view',
            schema_version='pr6_fact_view.v3', identity_field='view_id', identity=ref['view_id'])
        assert digest == ref['manifest_digest']
        _validate_manifest_identity(manifest, 'view_id', 'pr6-fact', ref['view_id'])
        assert manifest['snapshot_ref']['snapshot_id'] == before['snapshot']
        assert manifest['implementation_digests'] == code
        for entry in manifest['files']:
            content = _safe_path(root, target / entry['path']).read_bytes()
            assert len(content) == entry['size'] and _digest(content) == entry['content_digest']
            count += 1
        assert _digest((target / 'rows.json').read_bytes()) == expected['digest']
    return dict(status='PASS', artifacts=50, files=count, logical_equal=50,
                equivalent_rejections=len(before['rejections']), resume_builder_calls=0)


if __name__ == '__main__':
    print(json.dumps(check(Path(sys.argv[1]), Path(sys.argv[2])), indent=2))
