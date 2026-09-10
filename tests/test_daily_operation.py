"""Daily orchestration on a copied real closure with explicitly simulated collection."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import ArtifactError, SnapshotReader, daily, load_raw_batch


class DailyOperationTest(unittest.TestCase):
    def test_partial_collection_resume_t_plus_one_and_no_change(self):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        class Client:
            fail=True
            def __init__(self):self.calls=[];self.on_query=None
            def query(self,endpoint,**params):
                self.calls.append(endpoint)
                if self.on_query:
                    self.on_query();self.on_query=None
                if endpoint=='margin_detail':
                    if self.fail:raise RuntimeError('simulated supplier unavailable')
                    return [dict(ts_code='688981.SH',trade_date='20250613',rzye=12,rzmre=2,rzche=1,rqyl=3,rqchl=1,rqmcl=1,rzrqye=20)]
                return [dict(ts_code='688981.SH',ann_date='20250614',end_date='20250331',holder_num=12345)]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(run['source_root'],root)
            try:
                parent=SnapshotReader(root,run['refs']['snapshot_id'])
                requests=[]
                for domain,endpoint,start,end,policy in [
                    ('holder_count_events','stk_holdernumber','20250101','20250614','revision_scan'),
                    ('margin_daily','margin_detail','20250613','20250613','next_session_publication')]:
                    requests.append(dict(collector='pr7',domain=domain,endpoint=endpoint,
                        params=dict(ts_code='688981.SH',start_date=start,end_date=end),
                        economic_scope=dict(start=start,end=end),availability_policy=policy))
                inputs={r['domain']:dict(raw_batch_ids=[],contract_version=r['domain']+'.v1',config={},new_lineage=False) for r in requests}
                args=dict(snapshot_id=parent.snapshot.ref.snapshot_id,source_requests=requests,domain_inputs=inputs)
                client=Client()
                # This fixed clock is test simulation, never used for real supplier collection.
                with patch('axiom_data.pr7_source._retrieved_at',return_value='2025-06-14T06:00:00Z'):
                    failed=daily(root,run_id='daily',client=client,**args)
                    self.assertEqual(failed['status'],'FAILED');self.assertNotIn('snapshot_id',failed)
                    self.assertEqual(len(failed['collected_raw_batch_ids']),1)
                    self.assertFalse(failed['ready_for_consumption'])
                    client.fail=False
                    client.on_query=lambda:inputs['holder_count_events']['config'].update(bogus='invalid')
                    result=daily(root,run_id='daily',client=client,**args)
                    self.assertEqual(result['status'],'CANDIDATE_BUILT',result)
                    self.assertEqual(result['resolved_domain_inputs']['holder_count_events']['config'],{})
                    inputs['holder_count_events']['config'].clear()
                    self.assertEqual(client.calls.count('stk_holdernumber'),1)
                    self.assertEqual(result['changed_domains'],['holder_count_events','margin_daily'])
                    self.assertFalse(result['ready_for_consumption'])
                    margin_id=next(result['collected_raw_batch_ids'][r['request_id']] for r in result['plan']['source_plan']['source_requests'] if r['domain']=='margin_daily')
                    raw=load_raw_batch(root,margin_id)
                    self.assertEqual(json.loads(raw.payload)[0]['trade_date'],'20250613')
                    self.assertEqual(raw.manifest['retrieved_at'],'2025-06-14T06:00:00Z')
                    unchanged=daily(root,run_id='repeat',client=client,**dict(args,snapshot_id=result['snapshot_id']))
                    self.assertEqual(unchanged['status'],'NO_CHANGE',unchanged)
                    self.assertEqual(unchanged['snapshot_id'],result['snapshot_id'])
                    self.assertEqual(unchanged['changed_domains'],[])
                    changed=json.loads(json.dumps(inputs));changed['margin_daily']['new_lineage']=True
                    with self.assertRaisesRegex(ArtifactError,'resume daily plan'):
                        daily(root,run_id='daily',client=client,**dict(args,domain_inputs=changed))
                self.assertEqual(SnapshotReader(root,parent.snapshot.ref.snapshot_id).snapshot.manifest,parent.snapshot.manifest)
                self.assertFalse((root/'current.json').exists())
            finally:
                for p in root.rglob('*'):
                    if p.is_dir():p.chmod(0o755)
