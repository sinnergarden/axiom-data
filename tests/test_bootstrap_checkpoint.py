import json
import shutil
import tempfile
import unittest
from pathlib import Path

from axiom_data import ArtifactError, SnapshotReader, bootstrap


class BootstrapCheckpointTest(unittest.TestCase):
    def test_complete_checkpoint_reuses_snapshot_and_revalidates_resume(self):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(run['source_root'],root)
            try:
                reader=SnapshotReader(root,run['refs']['snapshot_id'])
                ids={d:c.ref.commit_id for d,c in reader.commits.items()}
                first=bootstrap(root,run_id='checkpoint',domain_commit_ids=ids)
                self.assertEqual(first['status'],'CANDIDATE_BUILT')
                self.assertEqual(first['snapshot_id'],run['refs']['snapshot_id'])
                self.assertFalse(first['ready_for_consumption'])
                self.assertEqual(bootstrap(root,run_id='checkpoint',domain_commit_ids=ids),first)
                with self.assertRaises(ArtifactError):bootstrap(root,run_id='missing',domain_commit_ids={})
                with self.assertRaises(ArtifactError):bootstrap(root,run_id='both',domain_commit_ids=ids,domain_inputs={})
                c=reader.commits['market_daily']
                path=root/'canonical/market_daily/commits'/c.ref.commit_id/'rows.json'
                path.chmod(0o600);path.write_bytes(path.read_bytes()+b' ')
                rejected=bootstrap(root,run_id='checkpoint',domain_commit_ids=ids)
                self.assertEqual(rejected['status'],'FAILED')
                self.assertNotIn('snapshot_id',rejected)
                self.assertFalse(rejected['ready_for_consumption'])
            finally:
                for p in root.rglob('*'):
                    if p.is_dir():p.chmod(0o755)
