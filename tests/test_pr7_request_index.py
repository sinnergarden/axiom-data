import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import ArtifactError, SnapshotReader, load_raw_batch
from axiom_data import pr7_views
from test_pr7_source import current_pr7_snapshot


class RequestCoverageIndexTest(unittest.TestCase):
    def test_history_nodes_are_loaded_once_then_query_reuses_reader_index(self):
        from collections import Counter
        from axiom_data import BuildApplication, create_snapshot, artifacts
        from axiom_data.pr7_source import Pr7Builder, Pr7Collector
        from test_pr6_artifacts import Client
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(run['source_root'],root)
            try:
                parent_id=current_pr7_snapshot(root,run['refs']['snapshot_id'],['holder_count_events'])
                initial=SnapshotReader(root,parent_id)
                ids={d:c.ref.commit_id for d,c in initial.commits.items()}
                domain='holder_count_events'
                old=initial.commits[domain]
                parent=old.ref.commit_id
                counts=[]
                for depth in range(1,7):
                    raw=Pr7Collector(root,Client([dict(ts_code='688981.SH',ann_date='20250401',
                        end_date='20250331',holder_num=200+depth)])).collect('stk_holdernumber',
                        {'ts_code':'688981.SH','start_date':'20250101','end_date':'20250630'},
                        retrieved_at=f'2026-09-{depth:02d}T00:00:00Z')
                    builder=Pr7Builder(root,domain,builder_config=old.manifest['builder_config'],
                        dependency_commit_ids={'security_master':ids['security_master']})
                    parent=BuildApplication(domain,builder).build(parent,[raw.raw_batch_id],[],old.ref.contract_version).commit_id
                    if depth not in (3,6):continue
                    snapshot=create_snapshot(root,dict(ids,**{domain:parent}))
                    with patch.object(artifacts,'load_domain_commit',wraps=artifacts.load_domain_commit) as loaded:
                        reader=SnapshotReader(root,snapshot.snapshot_id)
                        before=Counter((call.args[1],call.args[2]) for call in loaded.call_args_list)
                        self.assertTrue(all(n==1 for n in before.values()))
                        counts.append(sum(n for (d,_),n in before.items() if d==domain))
                        with patch.object(pr7_views,'load_raw_batch',wraps=load_raw_batch) as raw_loaded:
                            pr7_views.request_coverage(reader,domain,'688981.SH','2025-06-13')
                            first=raw_loaded.call_count
                            for _ in range(10):pr7_views.request_coverage(reader,domain,'688981.SH','2025-06-13')
                            self.assertEqual(raw_loaded.call_count,first)
                        self.assertEqual(len(loaded.call_args_list),sum(before.values()))
                self.assertEqual(counts[1]-counts[0],3)
            finally:
                for path in root.rglob('*'):
                    if path.is_dir():path.chmod(0o755)

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
