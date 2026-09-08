import json
from pathlib import Path
import shutil
import tempfile
import unittest
from axiom_data import SnapshotReader
from axiom_data.operations import assemble_candidate


class CandidateTest(unittest.TestCase):
    def test_candidate_is_not_promoted_and_failed_raw_is_retained(self):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_text())
        root=Path(tempfile.mkdtemp())/'data'
        try:
            shutil.copytree(run['source_root'],root)
            parent=SnapshotReader(root,run['refs']['snapshot_id'])
            old={d:c.ref.commit_id for d,c in parent.commits.items()}
            raw=[r['raw_batch_id'] for r in parent.commits['holder_count_events'].manifest['ordered_raw_batch_refs']]
            spec={'raw_batch_ids':raw,'contract_version':'holder_count_events.v1',
                  'config':{},'new_lineage':True}
            result=assemble_candidate(root,run_id='candidate',parent_snapshot_id=parent.snapshot.ref.snapshot_id,
                                      domain_inputs={'holder_count_events':spec})
            self.assertEqual(result['status'],'CANDIDATE_BUILT',result.get('failed'))
            self.assertFalse(result['ready_for_consumption'])
            self.assertFalse((root/'current.json').exists())
            new=SnapshotReader(root,result['snapshot_id'])
            for domain,identity in old.items():
                if domain!='holder_count_events': self.assertEqual(new.commits[domain].ref.commit_id,identity)
            again=assemble_candidate(root,run_id='candidate',parent_snapshot_id=parent.snapshot.ref.snapshot_id,
                                     domain_inputs={'holder_count_events':spec})
            self.assertEqual(again['snapshot_id'],result['snapshot_id'])
            failed=assemble_candidate(root,run_id='failed',parent_snapshot_id=parent.snapshot.ref.snapshot_id,
                domain_inputs={'financial_events':dict(spec,contract_version='financial_events.v2')})
            self.assertEqual(failed['status'],'FAILED')
            self.assertNotIn('snapshot_id',failed)
            self.assertTrue(all((root/'raw/batches'/identity).exists() for identity in raw))
            self.assertEqual(SnapshotReader(root,parent.snapshot.ref.snapshot_id).snapshot.manifest,
                             parent.snapshot.manifest)
        finally:
            for p in root.rglob('*'):
                if p.is_dir(): p.chmod(0o755)
            shutil.rmtree(root.parent)
