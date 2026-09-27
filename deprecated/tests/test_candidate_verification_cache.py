import json
import shutil
import tempfile
import unittest
from pathlib import Path
from fixture_locations import fixture_root
from axiom_data import ArtifactError,SnapshotReader,load_snapshot
from axiom_data.operations import assemble_candidate
from axiom_data.verification_cache import candidate_verification
from test_event_source import current_event_snapshot


class CandidateVerificationCacheTest(unittest.TestCase):
    def test_candidate_resume_preserves_identity_and_rechecks_source(self):
        run=json.loads(Path('deprecated/history/reports/pr7/run_manifest.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(fixture_root(run['source_root']),root)
            try:
                parent_id=current_event_snapshot(root,run['refs']['snapshot_id'],['margin_daily'])
                reader=SnapshotReader(root,parent_id);rid=reader.commits['margin_daily'].manifest['ordered_raw_batch_refs'][0]['raw_batch_id']
                inputs={'margin_daily':dict(raw_batch_ids=[rid],contract_version='margin_daily.v1',config={},new_lineage=False)}
                def build():return assemble_candidate(root,run_id='candidate',parent_snapshot_id=parent_id,domain_inputs=inputs)
                result=build();self.assertEqual(result['status'],'CANDIDATE_BUILT',result.get('failed'))
                self.assertEqual(build()['snapshot_id'],result['snapshot_id'])
                new=SnapshotReader(root,result['snapshot_id'])
                self.assertEqual(list(new.commits['margin_daily'].rows),list(reader.commits['margin_daily'].rows))
                raw=root/'raw/batches'/rid;m=json.loads((raw/'manifest.json').read_bytes());p=raw/m['payload_files'][0]['path']
                p.chmod(0o600);p.write_bytes(p.read_bytes()+b' ')
                failed=build()
                self.assertEqual(failed['status'],'FAILED')
                self.assertEqual(failed['failed']['margin_daily']['error_type'],'ArtifactError')
                with self.assertRaises(ArtifactError):load_snapshot(root,result['snapshot_id'])
            finally:
                for p in root.rglob('*'):
                    if p.is_dir():p.chmod(0o755)

    def test_same_identity_in_another_root_is_not_a_cache_hit_and_failure_unwinds(self):
        run=json.loads(Path('deprecated/history/reports/pr7/run_manifest.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            a=Path(directory)/'a';b=Path(directory)/'b'
            for root in [a,b]:shutil.copytree(fixture_root(run['source_root']),root)
            try:
                reader=SnapshotReader(b,run['refs']['snapshot_id'])
                rid=reader.commits['margin_daily'].manifest['ordered_raw_batch_refs'][0]['raw_batch_id']
                raw=b/'raw/batches'/rid;m=json.loads((raw/'manifest.json').read_bytes());relative=raw.relative_to(b)/m['payload_files'][0]['path']
                p=b/relative;p.chmod(0o600);p.write_bytes(p.read_bytes()+b' ')
                @candidate_verification
                def check(root):
                    load_snapshot(root,run['refs']['snapshot_id'])
                    load_snapshot(root,run['refs']['snapshot_id'])
                    load_snapshot(b,run['refs']['snapshot_id'])
                with self.assertRaises(ArtifactError):check(a)
                p=a/relative;p.chmod(0o600);p.write_bytes(p.read_bytes()+b' ')
                with self.assertRaises(ArtifactError):load_snapshot(a,run['refs']['snapshot_id'])
            finally:
                for root in [a,b]:
                    for p in root.rglob('*'):
                        if p.is_dir():p.chmod(0o755)
