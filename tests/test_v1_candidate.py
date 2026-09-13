import json
from pathlib import Path
from fixture_locations import fixture_root
import shutil
import tempfile
import unittest
from axiom_data import SnapshotReader, repair
from axiom_data.operations import assemble_candidate


class CandidateTest(unittest.TestCase):
    def test_candidate_is_not_promoted_and_failed_raw_is_retained(self):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_text())
        root=Path(tempfile.mkdtemp())/'data'
        try:
            shutil.copytree(fixture_root(run['source_root']),root)
            parent=SnapshotReader(root,run['refs']['snapshot_id'])
            old={d:c.ref.commit_id for d,c in parent.commits.items()}
            raw=[r['raw_batch_id'] for r in parent.commits['holder_count_events'].manifest['ordered_raw_batch_refs']]
            spec={'raw_batch_ids':raw,'contract_version':'holder_count_events.v1',
                  'config':{},'new_lineage':True}
            result=repair(root,run_id='candidate',snapshot_id=parent.snapshot.ref.snapshot_id,
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
            nochange_spec=dict(spec,new_lineage=False)
            nochange=assemble_candidate(root,run_id='nochange',parent_snapshot_id=result['snapshot_id'],
                                       domain_inputs={'holder_count_events':nochange_spec})
            self.assertEqual(nochange['domain_commit_ids']['holder_count_events'],new.commits['holder_count_events'].ref.commit_id)
            resumed_nochange=assemble_candidate(root,run_id='nochange',parent_snapshot_id=result['snapshot_id'],
                                                domain_inputs={'holder_count_events':nochange_spec})
            self.assertEqual(resumed_nochange['snapshot_id'],nochange['snapshot_id'])
            # Another valid commit is not evidence that this request completed.
            state_path=root/'operations/candidate/build.json'
            tampered=json.loads(state_path.read_bytes())
            tampered['published_commits']['holder_count_events']=old['holder_count_events']
            state_path.write_text(json.dumps(tampered))
            rejected=assemble_candidate(root,run_id='candidate',parent_snapshot_id=parent.snapshot.ref.snapshot_id,
                                        domain_inputs={'holder_count_events':spec})
            self.assertEqual(rejected['status'],'FAILED')
            self.assertNotIn('snapshot_id',rejected)
            self.assertNotIn('domain_commit_ids',rejected)
            self.assertFalse(rejected['ready_for_consumption'])
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

    def test_repair_requires_explicit_snapshot_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for identity in ['current','latest',None]:
                with self.assertRaises(ValueError):
                    repair(root,run_id='repair',snapshot_id=identity,domain_inputs={})
            self.assertEqual(list(root.iterdir()),[])
