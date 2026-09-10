import json
from pathlib import Path
import tempfile
import unittest
from axiom_data import ArtifactError,load_raw_batch
from axiom_data.pr7_source import Pr7Collector,normalize,validate_payload


class HolderTimestampTest(unittest.TestCase):
    def test_real_supplier_timestamp_keeps_raw_and_precise_availability(self):
        fixture=json.loads(Path('tests/fixtures/holder_timestamp.json').read_bytes())
        params={'ts_code':'000001.SZ','start_date':'20140101','end_date':'20260908'}
        with self.assertRaises(ArtifactError):validate_payload('stk_holdernumber',params,fixture['rows'])
        class Client:
            def query(self,*args,**kwargs):return fixture['rows']
        with tempfile.TemporaryDirectory() as root:
            ref=Pr7Collector(root,Client()).collect('stk_holdernumber',params,profile_version='tushare_pr7_holder.v2',retrieved_at='2026-09-10T09:00:00Z')
            raw=load_raw_batch(root,ref.raw_batch_id)
            self.assertEqual(json.loads(raw.payload),fixture['rows'])
            rows=normalize(raw,'holder_count_events')
            row=next(r for r in rows if r['report_period']=='2025-05-07')
            self.assertEqual(row['announcement'],'2025-05-12')
            self.assertEqual(row['vendor_available_at'],'2025-05-12T15:09:08+08:00')
            self.assertIsNone(row['values']['number']);self.assertEqual(row['pit_qualification'],'best_effort')
            self.assertEqual(row['source_ref'],ref.raw_batch_id)
        for value in ['2025-02-30 15:09:08','2025-05-12 25:00:00','2025-05-12T15:09:08Z']:
            wrong=[dict(fixture['rows'][0],ann_date=value)]
            with self.assertRaises(ArtifactError):validate_payload('stk_holdernumber',params,wrong,profile_version='tushare_pr7_holder.v2')
