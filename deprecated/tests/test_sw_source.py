import json
from pathlib import Path
import tempfile
import unittest
from axiom_data import ArtifactError, load_raw_batch
from axiom_data.sw_source import SwQualificationCollector, validate_request, payload_issues, load_profile
from axiom_data.fundamentals_source import FundamentalsBuilder
from axiom_data.contracts import load_contract


class Client:
    def query(self, endpoint, *, fields, **params):
        return [dict(zip(fields.split(','), ['801160.SI','公用事业','801161.SI','电力','851161.SI','风力发电','001289.SZ','龙源电力','20220124',None,'Y']))]


class SwSourceTest(unittest.TestCase):
    def test_frozen_anomalous_observation_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            ref=SwQualificationCollector(root,Client()).collect('index_member_all',{'ts_code':'001289.SZ','is_new':'N'})
            raw=load_raw_batch(root,ref.raw_batch_id)
            self.assertEqual(raw.manifest['summary']['qualification'],'BLOCKED')
            self.assertIn('request_scope_mismatch:is_new',raw.manifest['summary']['qualification_issues'])
            with self.assertRaises(ArtifactError):
                FundamentalsBuilder(root,'industry_membership')._build_rows(load_contract('industry_membership.v2'),[],[raw])

    def test_request_scope_and_truncation(self):
        for params in ({},{'ts_code':'001289.SZ','token':'private'},{'ts_code':'001289.SZ','is_new':'ALL'},{'l3_code':'bad'}):
            with self.assertRaises(ArtifactError): validate_request('index_member_all',params)
        row=Client().query('index_member_all',fields=','.join(load_profile()['endpoints']['index_member_all']['fields']))[0]
        self.assertEqual(payload_issues('index_member_all',{'ts_code':'001289.SZ'},[row]),[])
        self.assertIn('possible_truncation',payload_issues('index_member_all',{'ts_code':'001289.SZ'},[row]*2000))
