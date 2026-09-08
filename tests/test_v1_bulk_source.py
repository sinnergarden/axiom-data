import json
from pathlib import Path
import tempfile
import unittest
from axiom_data import ArtifactError, load_raw_batch
from axiom_data.contracts import load_contract
from axiom_data.pr6_source import Pr6Collector, Pr6Builder, validate_payload


class Client:
    def query(self, *args, **kwargs):
        return [{'ts_code': '600036.SH', 'trade_date': '20260908', 'industry': '银行'},
                {'ts_code': '000001.SZ', 'trade_date': '20260908', 'industry': '银行'}]


class BulkSourceTest(unittest.TestCase):
    def test_v2_batch_mapping_and_v1_dispatch(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);collector=Pr6Collector(root,Client())
            ref=collector.collect('bak_basic',{'trade_date':'20260908'},profile_version='tushare_pr6.v2')
            raw=load_raw_batch(root,ref.raw_batch_id)
            rows=Pr6Builder(root,'industry_membership',builder_config={'symbols':['600036.SH']})._build_rows(
                load_contract('industry_membership.v2'),[],[raw])
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['symbol'],'600036.SH')
            self.assertEqual(rows[0]['industry_id'],'银行')
            self.assertEqual(rows[0]['effective_to'],'2026-09-09')
            with self.assertRaises(ArtifactError):
                collector.collect('bak_basic',{'trade_date':'20260908'})

    def test_limit_and_wrong_date_fail_closed(self):
        row=Client().query()[0]
        for values in ([row]*7000,[dict(row,trade_date='20260907')]):
            with self.assertRaises(ArtifactError):
                validate_payload('bak_basic',{'trade_date':'20260908'},values,profile_version='tushare_pr6.v2')
