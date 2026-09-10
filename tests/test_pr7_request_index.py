import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import ArtifactError, SnapshotReader, load_raw_batch
from axiom_data import pr7_views


class RequestCoverageIndexTest(unittest.TestCase):
    def test_reader_local_intervals_keep_scope_and_new_reader_integrity(self):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(run['source_root'],root)
            try:
                reader=SnapshotReader(root,run['refs']['snapshot_id'])
                domain='margin_daily'
                with patch.object(pr7_views,'load_raw_batch',wraps=load_raw_batch) as loaded:
                    pr7_views.request_coverage(reader,domain,'688981.SH','2025-06-13')
                    first=loaded.call_count;self.assertGreater(first,0)
                    for _ in range(100):
                        pr7_views.request_coverage(reader,domain,'688981.SH','2025-06-13')
                    self.assertEqual(loaded.call_count,first)
                    with self.assertRaisesRegex(ArtifactError,'INSUFFICIENT_SCOPE'):
                        pr7_views.request_coverage(reader,domain,'688981.SH','2099-01-01')
                    with self.assertRaisesRegex(ArtifactError,'unknown security'):
                        pr7_views.request_coverage(reader,domain,'999999.SH','2025-06-13')
                    other=SnapshotReader(root,run['refs']['snapshot_id'])
                    pr7_views.request_coverage(other,domain,'688981.SH','2025-06-13')
                    self.assertEqual(loaded.call_count,first*2)
                raw_id=reader.commits[domain].manifest['ordered_raw_batch_refs'][0]['raw_batch_id']
                raw=load_raw_batch(root,raw_id)
                payload=root/'raw/batches'/raw_id/raw.manifest['payload_files'][0]['path']
                payload.chmod(0o600);payload.write_bytes(raw.payload+b' ')
                with self.assertRaises(ArtifactError):SnapshotReader(root,run['refs']['snapshot_id'])
            finally:
                for path in root.rglob('*'):
                    if path.is_dir():path.chmod(0o755)
