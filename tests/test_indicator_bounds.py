import json
from pathlib import Path
import tempfile
import unittest
from axiom_data import ArtifactError,load_raw_batch
from axiom_data.pr6_source import Pr6Collector,Pr6Builder,validate_payload
from axiom_data.contracts import load_contract


class IndicatorBoundsTest(unittest.TestCase):
    def test_report_query_bounds_do_not_backdate_announcement_availability(self):
        fixture=json.loads(Path('tests/fixtures/fina_indicator_bounds.json').read_bytes())
        params={'ts_code':'000001.SZ','start_date':'20130101','end_date':'20171231'}
        with self.assertRaises(ArtifactError):validate_payload('fina_indicator',params,fixture['rows'])
        class Client:
            def query(self,*args,**kwargs):return fixture['rows']
        with tempfile.TemporaryDirectory() as root:
            ref=Pr6Collector(root,Client()).collect('fina_indicator',params,profile_version='tushare_fina_indicator.v1')
            raw=load_raw_batch(root,ref.raw_batch_id)
            self.assertEqual(json.loads(raw.payload),fixture['rows'])
            rows=Pr6Builder(root,'financial_events')._build_rows(load_contract('financial_events.v2'),[],[raw])
            yearend=next(r for r in rows if r['report_period']=='2017-12-31')
            self.assertEqual(yearend['vendor_available_at'],'2018-03-15T23:59:59+08:00')
            self.assertEqual(yearend['pit_qualification'],'best_effort')
            wrong=[dict(fixture['rows'][0],end_date='20181231')]
            with self.assertRaises(ArtifactError):validate_payload('fina_indicator',params,wrong,profile_version='tushare_fina_indicator.v1')
