import json
import shutil
import tempfile
import unittest
from pathlib import Path

from axiom_data import ArtifactError, SnapshotReader, daily, load_raw_batch, plan_daily
from axiom_data.operations import collect_requests
from test_pr7_source import current_pr7_snapshot


class ObservedRawReuseTest(unittest.TestCase):
    def test_daily_reuses_exact_observations_without_supplier_or_time_changes(self):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        class NoSource:
            calls=0
            def query(self,*args,**kwargs):
                self.calls+=1;raise AssertionError('precollected observation must not query supplier')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(run['source_root'],root)
            try:
                old=SnapshotReader(root,run['refs']['snapshot_id'])
                parent_id=current_pr7_snapshot(root,run['refs']['snapshot_id'],['margin_daily'])
                ids=[v['raw_batch_id'] for v in old.commits['margin_daily'].manifest['ordered_raw_batch_refs']]
                raw=load_raw_batch(root,ids[0]);m=raw.manifest;params=m['request']['params']
                spec=dict(collector='pr7',domain='margin_daily',endpoint='margin_detail',params=params,
                    economic_scope=dict(start=params['start_date'],end=params['end_date']),availability_policy='next_session_publication')
                planned=plan_daily(root,parent_id,source_requests=[spec]);key=planned['source_requests'][0]['request_id']
                observed={key:ids[0]};client=NoSource();count=len(list((root/'raw/batches').iterdir()))
                args=dict(source_requests=[spec],domain_inputs={'margin_daily':dict(raw_batch_ids=[],contract_version='margin_daily.v1',config={},new_lineage=False)},
                          observed_raw_batch_ids=observed,client=client)
                result=daily(root,run_id='observed-daily',snapshot_id=parent_id,**args)
                self.assertEqual(result['status'],'CANDIDATE_BUILT',result.get('failed'))
                self.assertEqual(result['collected_raw_batch_ids'],observed)
                same=daily(root,run_id='observed-no-change',snapshot_id=result['snapshot_id'],**args)
                self.assertEqual(same['status'],'NO_CHANGE',same.get('failed'))
                self.assertEqual(same['snapshot_id'],result['snapshot_id'])
                self.assertEqual(client.calls,0);self.assertEqual(len(list((root/'raw/batches').iterdir())),count)
                self.assertEqual(load_raw_batch(root,ids[0]),raw)
                self.assertEqual(SnapshotReader(root,run['refs']['snapshot_id']).snapshot.manifest,old.snapshot.manifest)
                with self.assertRaisesRegex(ArtifactError,'binding mismatch'):
                    collect_requests(root,run_id='wrong-source',requests=[spec],observed_raw_batch_ids={key:ids[1]},client=client)
                record=root/'operations/observed-daily/collection.json';state=json.loads(record.read_bytes());state['completed'][key]=ids[1];record.write_text(json.dumps(state))
                with self.assertRaisesRegex(ArtifactError,'explicitly bound'):
                    collect_requests(root,run_id='observed-daily',requests=[spec],observed_raw_batch_ids=observed,client=client)
                self.assertEqual(json.loads(record.read_bytes())['status'],'FAILED')
                with self.assertRaisesRegex(ArtifactError,'planned request IDs'):
                    collect_requests(root,run_id='wrong-key',requests=[spec],observed_raw_batch_ids={'unknown':ids[0]},client=client)
                self.assertEqual(client.calls,0)
            finally:
                for p in root.rglob('*'):
                    if p.is_dir():p.chmod(0o755)
