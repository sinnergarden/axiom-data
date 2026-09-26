import json
import shutil
import tempfile
import unittest
from pathlib import Path
from fixture_locations import fixture_root
from axiom_data import FactView, ArtifactError, SnapshotReader, load_snapshot
from axiom_data.operations import compare_event_projection


class AcceptanceReadReuseTest(unittest.TestCase):
    def test_public_acceptance_repeats_and_rejects_consumed_corruption(self):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes());refs=run['refs']
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(fixture_root(run['source_root']),root)
            def facts():
                return FactView(root,refs['snapshot_id'],financial_fact_view_id=refs['pr6_view_id'],adjusted_price_view_id=refs['adjusted_view_id'])
            def compare():
                return compare_event_projection(root,refs['snapshot_id'],refs['pr7_view_id'],symbols=['688981.SH'],fields=['holder.number','holder.top10_ratio','margin.balance'],start_session='2025-06-10',end_session='2025-06-13')
            try:
                fact=facts()
                self.assertEqual(fact.financial.ref.view_id,refs['pr6_view_id'])
                self.assertEqual(fact.adjusted.ref.view_id,refs['adjusted_view_id'])
                result=compare()
                self.assertEqual(result['status'],'PASS');self.assertEqual(result['rows'],4)
                self.assertEqual(compare(),result)
                reader=SnapshotReader(root,refs['snapshot_id'])
                commit=reader.commits['margin_daily']
                rid=commit.manifest['ordered_raw_batch_refs'][0]['raw_batch_id']
                raw=root/'raw/batches'/rid;manifest=json.loads((raw/'manifest.json').read_bytes())
                payload=raw/manifest['payload_files'][0]['path'];payload.chmod(0o600);payload.write_bytes(payload.read_bytes()+b' ')
                # Ordinary reads use published canonical bytes; full admission
                # additionally replays the Raw closure and must reject damage.
                with self.assertRaises(ArtifactError):load_snapshot(root,refs['snapshot_id'])
                self.assertEqual(compare(),result)
                output=root/'canonical/margin_daily/commits'/commit.ref.commit_id/commit.manifest['output_files'][0]['path']
                output.chmod(0o600);output.write_bytes(output.read_bytes()+b' ')
                with self.assertRaises(ArtifactError):compare()
            finally:
                for p in root.rglob('*'):
                    if p.is_dir():p.chmod(0o755)
