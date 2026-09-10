import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from axiom_data import FactView, ArtifactError, artifacts
from axiom_data.operations import compare_pr7_projection


class AcceptanceReadReuseTest(unittest.TestCase):
    def test_public_acceptance_reads_validate_once_and_reject_later_corruption(self):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes());refs=run['refs']
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(run['source_root'],root)
            def facts():
                return FactView(root,refs['snapshot_id'],pr6_fact_view_id=refs['pr6_view_id'],adjusted_price_view_id=refs['adjusted_view_id'])
            def compare():
                return compare_pr7_projection(root,refs['snapshot_id'],refs['pr7_view_id'],symbols=['688981.SH'],fields=['holder.number','holder.top10_ratio','margin.balance'],start_session='2025-06-10',end_session='2025-06-13')
            try:
                with patch.object(artifacts,'load_domain_commit',wraps=artifacts.load_domain_commit) as loaded:
                    fact=facts();self.assertEqual(loaded.call_count,18)
                    self.assertEqual(fact.pr6.ref.view_id,refs['pr6_view_id'])
                    self.assertEqual(fact.adjusted.ref.view_id,refs['adjusted_view_id'])
                    result=compare();self.assertEqual(loaded.call_count,36)
                    self.assertEqual(result['status'],'PASS');self.assertEqual(result['rows'],4)
                raw=next((root/'raw/batches').iterdir());manifest=json.loads((raw/'manifest.json').read_bytes())
                payload=raw/manifest['payload_files'][0]['path'];payload.chmod(0o600);payload.write_bytes(payload.read_bytes()+b' ')
                with self.assertRaises(ArtifactError):facts()
                with self.assertRaises(ArtifactError):compare()
            finally:
                for p in root.rglob('*'):
                    if p.is_dir():p.chmod(0o755)
