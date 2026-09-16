import json
from pathlib import Path
import tempfile
import unittest
from axiom_data import ArtifactError,load_raw_batch
from axiom_data.pr7_source import Pr7Collector,normalize,validate_payload,canonicalization_report


class HolderTimestampTest(unittest.TestCase):
    def test_unkeyed_empty_records_are_explicitly_qualified_without_fake_periods(self):
        for fixture in json.loads(Path('tests/fixtures/holder_unkeyed_empty.json').read_bytes()):
            params={'ts_code':fixture['rows'][0]['ts_code'],'start_date':'20140101','end_date':'20260908'}
            with self.assertRaises(ArtifactError):validate_payload('stk_holdernumber',params,fixture['rows'],profile_version='tushare_pr7_holder.v2')
            class Client:
                def query(self,*args,**kwargs):return fixture['rows']
            with tempfile.TemporaryDirectory() as root:
                ref=Pr7Collector(root,Client()).collect('stk_holdernumber',params,profile_version='tushare_pr7_holder.v3')
                raw=load_raw_batch(root,ref.raw_batch_id)
                self.assertEqual(json.loads(raw.payload),fixture['rows'])
                excluded=raw.manifest['summary']['source_qualification']
                self.assertEqual(len(excluded),1)
                self.assertEqual(excluded[0]['reason'],'unkeyed_empty_observation')
                self.assertNotIn('canonical_admission',raw.manifest['summary'])
                replay=Pr7Collector(root,Client()).collect('stk_holdernumber',params,
                    profile_version='tushare_pr7_holder.v3',retrieved_at=raw.manifest['retrieved_at'])
                self.assertEqual(replay.raw_batch_id,ref.raw_batch_id)
                rows=normalize(raw,'holder_count_events')
                self.assertTrue(all(r['report_period'] for r in rows))
                broken=dict(fixture['rows'][excluded[0]['row_index']],holder_num=123)
                validate_payload('stk_holdernumber',params,[broken],profile_version='tushare_pr7_holder.v3')

    def test_missing_report_date_preserves_raw_and_reports_rejection(self):
        from types import SimpleNamespace
        from copy import deepcopy
        from axiom_data.source_completeness import validate_raw_completeness
        fixture=json.loads(Path('tests/fixtures/holder_missing_report_date.json').read_bytes())
        records=fixture['records'];params=fixture['params']
        class Client:
            def query(self,*args,**kwargs):return records
        with tempfile.TemporaryDirectory() as root:
            ref=Pr7Collector(root,Client()).collect('stk_holdernumber',params,profile_version='tushare_pr7_holder.v3')
            raw=load_raw_batch(root,ref.raw_batch_id)
            self.assertEqual(json.loads(raw.payload),records)
            self.assertEqual(len(records),204)
            validate_raw_completeness(raw)
            report=canonicalization_report(raw)
            self.assertEqual(report['raw_ref'],ref.raw_batch_id)
            self.assertEqual(report['unmaterializable_count'],1)
            rejection=report['rejected_rows'][0]
            self.assertEqual(rejection['reason'],'MISSING_REPORT_DATE')
            self.assertEqual(rejection['affected_field'],'end_date')
            self.assertEqual(rejection['status'],'unmaterializable')
            self.assertEqual(records[rejection['row_index']]['holder_num'],30274)
            self.assertTrue(rejection['row_fingerprint'])
            rows=normalize(raw,'holder_count_events')
            self.assertEqual(len(rows),203)
            self.assertEqual(raw.manifest['summary']['source_qualification'],[])
            # The original mapper path for the 203 valid rows must be identical.
            manifest=deepcopy(raw.manifest)
            manifest['summary'].pop('canonical_admission')
            valid=[r for r in records if r['end_date'] is not None]
            baseline=SimpleNamespace(manifest=manifest,payload=json.dumps(valid).encode(),ref=raw.ref)
            self.assertEqual(rows,normalize(baseline,'holder_count_events'))
            for date in ['20240230','not-a-date']:
                with self.assertRaises(ArtifactError):
                    validate_payload('stk_holdernumber',params,[dict(records[0],end_date=date)],profile_version='tushare_pr7_holder.v3')

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
