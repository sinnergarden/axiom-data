import json
import shutil
import tempfile
import unittest
from pathlib import Path
from fixture_locations import fixture_root
from unittest.mock import patch

from axiom_data import ArtifactError, SnapshotReader, rebuild_catalog, validate_domain_commit_closure
from axiom_data import artifacts
from test_artifacts import write_rows, build_commit, synthetic_source_fixture


@synthetic_source_fixture
class CatalogValidationReuseTest(unittest.TestCase):
    def test_cache_is_local_and_failed_rebuild_preserves_catalog(self):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(fixture_root(run['source_root']),root)
            try:
                with patch.object(artifacts,'load_domain_commit',wraps=artifacts.load_domain_commit) as loaded:
                    self.assertEqual(rebuild_catalog(root),130)
                    self.assertEqual(loaded.call_count,18)
                    self.assertEqual(rebuild_catalog(root),130)
                    self.assertEqual(loaded.call_count,36)
                before=(root/'catalog.sqlite').read_bytes()
                reader=SnapshotReader(root,run['refs']['snapshot_id']);cid=reader.commits['market_daily'].ref.commit_id
                data=root/'canonical/market_daily/commits'/cid/'rows.json'
                data.chmod(0o600);data.write_bytes(data.read_bytes()+b' ')
                with self.assertRaises(ArtifactError):rebuild_catalog(root)
                self.assertEqual((root/'catalog.sqlite').read_bytes(),before)
            finally:
                for p in root.rglob('*'):
                    if p.is_dir():p.chmod(0o755)

    def test_checked_commits_cannot_bypass_snapshot_fixed_dependencies(self):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(fixture_root(run['source_root']),root)
            try:
                reader=SnapshotReader(root,run['refs']['snapshot_id'])
                write_rows(root,'catalog-calendar','trading_calendar',reader.trading_calendar())
                ref=build_commit(root,'trading_calendar',['catalog-calendar'])
                other=validate_domain_commit_closure(root,'trading_calendar',ref.commit_id)
                manifest=json.loads(json.dumps(reader.snapshot.manifest))
                manifest['domain_refs']['trading_calendar']=artifacts._commit_ref(other)
                digest=artifacts._identity_digest(manifest,'snapshot_id');identity=artifacts._derived_identity('snapshot',digest)
                manifest.update(snapshot_id=identity,identity_digest=digest)
                layout=artifacts._layout(root)
                artifacts._publish_directory(layout,layout.snapshots/identity,lambda path:artifacts._write_manifest(path,manifest))
                with self.assertRaisesRegex(ArtifactError,'fixed dependencies'):rebuild_catalog(root)
                self.assertEqual(SnapshotReader(root,run['refs']['snapshot_id']).snapshot.manifest,reader.snapshot.manifest)
            finally:
                for p in root.rglob('*'):
                    if p.is_dir():p.chmod(0o755)
