import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import ArtifactError, BuildApplication, MarketDomainBuilder, load_raw_batch, write_raw_batch
from axiom_data.artifacts import _digest, _json_bytes
from axiom_data.bootstrap_sources import plan_truncated_raw_split
from axiom_data.pr6_source import Pr6Builder, Pr6Collector, load_pr6_source_profile, profile_digest, validate_payload
from axiom_data.source_completeness import (SourceCompletenessError, completeness_policy,
                                          validate_raw_completeness)
from test_artifacts import security_row, write_rows, synthetic_source_profiles


class SourceCompletenessTest(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)

    def raw(self, count, *, version='tushare_fina_indicator.v1', status='success', identity=None,
            params=None, summary=None):
        params=params or {'ts_code':'000001.SZ','start_date':'20250101','end_date':'20250630'}
        rows=[{'ts_code':'000001.SZ','ann_date':'20250401','end_date':'20250331',
               'current_ratio':i+1} for i in range(count)]
        return write_raw_batch(self.root,identity or version+'-'+str(count),domain='financial_events',
            source_profile='tushare.pr6.fina_indicator',source_profile_version=version,
            source_profile_digest=profile_digest(version),request={'endpoint':'fina_indicator',
                'params':params,'fields':load_pr6_source_profile(version)['endpoints']['fina_indicator']['fields']},
            retrieved_at='2025-07-01T00:00:00Z',payload=_json_bytes(rows),collector_code='fixture',
            status=status,summary={'rows':count,**(summary or {})})

    def build(self, ref):
        if not getattr(self, '_synthetic_sources_active', False):
            self.enterContext(synthetic_source_profiles())
            self._synthetic_sources_active = True
        security_raw=write_rows(self.root,'security','security_master',[security_row()],
                                retrieved_at='2025-01-01T00:00:00Z')
        security=BuildApplication('security_master',MarketDomainBuilder(self.root,'security_master')).build(
            None,[security_raw.raw_batch_id],[],'security_master.v1')
        return BuildApplication('financial_events',Pr6Builder(self.root,'financial_events',
            dependency_commit_ids={'security_master':security.commit_id})).build(
                None,[ref.raw_batch_id],[],'financial_events.v2')

    def test_direct_public_builder_rejects_100_raw_for_current_and_legacy_profiles(self):
        for version in ('tushare_fina_indicator.v1','tushare_pr6.v1'):
            ref=self.raw(100,version=version)
            raw=load_raw_batch(self.root,ref.raw_batch_id)
            with self.assertRaises(SourceCompletenessError):self.build(ref)
            self.assertEqual(load_raw_batch(self.root,ref.raw_batch_id).payload,raw.payload)
        self.assertEqual(list((self.root/'canonical/financial_events/commits').glob('*/manifest.json')),[])

    def test_99_passes_profile_admission_and_build(self):
        ref=self.raw(99);raw=load_raw_batch(self.root,ref.raw_batch_id)
        result=validate_raw_completeness(raw)
        self.assertTrue(result['complete']);self.assertEqual(result['row_count'],99)
        self.assertEqual(result['policy_ref'],'source_completeness.v1#fina_indicator')
        self.build(ref)

    def test_direct_payload_and_partial_raw_cannot_bypass(self):
        raw=load_raw_batch(self.root,self.raw(100).raw_batch_id)
        with self.assertRaises(SourceCompletenessError):
            validate_payload('fina_indicator',raw.manifest['request']['params'],json.loads(raw.payload),
                             profile_version='tushare_fina_indicator.v1')
        partial=self.raw(1,status='partial',identity='partial')
        with self.assertRaises(ArtifactError):self.build(partial)

    def test_success_status_does_not_override_partial_summary(self):
        for index,summary in enumerate(({'complete':False},{'truncated':True},{'partial':True},
                                        {'completeness':'PARTIAL'})):
            ref=self.raw(1,identity='summary-'+str(index),summary=summary)
            with self.assertRaises(SourceCompletenessError):
                validate_raw_completeness(load_raw_batch(self.root,ref.raw_batch_id))
            with self.assertRaises(SourceCompletenessError):self.build(ref)

    def test_official_exchange_originals_keep_their_document_completeness_proof(self):
        from axiom_data.exchange_security import publish_termination, profile
        fixture=Path(__file__).parent/'fixtures'
        for exchange,name,count in (('SSE','sse-delist.json',159),('SZSE','szse-delist.xlsx',208)):
            payload=(fixture/name).read_bytes()
            ref=publish_termination(self.root,exchange=exchange,payload=payload,
                                    retrieved_at='2026-09-09T02:00:00Z')
            raw=load_raw_batch(self.root,ref.raw_batch_id)
            admission=validate_raw_completeness(raw)
            self.assertEqual(admission['row_count'],count)
            self.assertEqual(admission['completeness_rule'],'official_termination_document')
            self.assertIsNone(admission['limit'])
            self.assertTrue(admission['complete'])
            self.assertEqual(load_raw_batch(self.root,ref.raw_batch_id).payload,payload)
            wrong=write_raw_batch(self.root,'wrong-'+exchange,domain='security_master',
                source_profile='exchange.termination.'+exchange,source_profile_version='exchange_security.v1',
                source_profile_digest=_digest(b'wrong authority'),request=profile()['endpoints'][exchange],
                retrieved_at='2026-09-09T02:00:00Z',payload=payload,collector_code='fixture',summary={})
            with self.assertRaisesRegex(SourceCompletenessError,'binding mismatch'):
                validate_raw_completeness(load_raw_batch(self.root,wrong.raw_batch_id))

    def test_collector_retains_raw_before_rejection(self):
        source=load_raw_batch(self.root,self.raw(100).raw_batch_id)
        class Client:
            def query(self,*args,**kwargs):return json.loads(source.payload)
        with self.assertRaises(SourceCompletenessError) as raised:
            Pr6Collector(self.root,Client()).collect('fina_indicator',source.manifest['request']['params'],
                profile_version='tushare_fina_indicator.v1',retrieved_at='2025-08-01T00:00:00Z')
        preserved=load_raw_batch(self.root,raised.exception.raw_batch_id)
        self.assertEqual(preserved.payload,source.payload)

    def test_observed_raw_resume_revalidates_shared_admission(self):
        from axiom_data.operations import _request, collect_requests
        raw=load_raw_batch(self.root,self.raw(100).raw_batch_id)
        spec={'collector':'pr6_indicator','domain':'financial_events','endpoint':'fina_indicator',
              'params':raw.manifest['request']['params'],
              'economic_scope':{'start':'20250101','end':'20250630'},'availability_policy':'revision_scan'}
        with self.assertRaises(SourceCompletenessError):
            collect_requests(self.root,run_id='bound-cap',requests=[spec],
                             observed_raw_batch_ids={_request(spec):raw.ref.raw_batch_id})

    def test_split_is_deterministic_bounded_and_single_day_fails(self):
        raw=load_raw_batch(self.root,self.raw(100).raw_batch_id)
        plan=plan_truncated_raw_split(raw)
        self.assertEqual(plan,plan_truncated_raw_split(raw))
        self.assertEqual(plan['status'],'NEEDS_COLLECTION')
        self.assertEqual([(r['params']['start_date'],r['params']['end_date']) for r in plan['requests']],
                         [('20250101','20250401'),('20250402','20250630')])
        ref=self.raw(100,identity='one-day',params={'ts_code':'000001.SZ','start_date':'20250331','end_date':'20250331'})
        with self.assertRaisesRegex(ArtifactError,'single-day'):
            plan_truncated_raw_split(load_raw_batch(self.root,ref.raw_batch_id))

    def test_missing_policy_and_unknown_caps_are_not_complete(self):
        self.assertEqual(completeness_policy('tushare_pr6.v1','income')['completeness_rule'],'documented_security_history')
        self.assertEqual(completeness_policy('fixture.v1','fina_indicator')['status'],'unestablished')
        directory=self.root/'profiles';directory.mkdir()
        (directory/'tushare_pr6.v1.json').write_bytes(_json_bytes(load_pr6_source_profile()))
        with patch('axiom_data.source_completeness.files',return_value=directory):
            self.assertEqual(completeness_policy('tushare_pr6.v1','fina_indicator')['status'],'unestablished')

    def test_indicator_rejects_invented_pagination(self):
        raw=load_raw_batch(self.root,self.raw(100,params={'ts_code':'000001.SZ',
            'start_date':'20250101','end_date':'20250630','limit':'100','offset':'0'}).raw_batch_id)
        with self.assertRaisesRegex(SourceCompletenessError,'does not support pagination'):
            validate_raw_completeness(raw,evidence=[raw])

    def test_real_declared_pagination_requires_bound_terminal_pages(self):
        from axiom_data.sw_source import load_profile, profile_digest as sw_digest
        version='tushare_industry_qualification.v1';profile=load_profile(version)
        def page(offset,count,*,summary=None,suffix=''):
            records=[{'ts_code':f'{i+1:06d}.SZ','name':'fixture','l1_code':'801160.SI',
                      'l1_name':'公用事业','l2_code':'801161.SI','l2_name':'电力',
                      'l3_code':'851161.SI','l3_name':'风力发电',
                      'in_date':'20220124','out_date':'20250101','is_new':'N'}
                     for i in range(offset,offset+count)]
            ref=write_raw_batch(self.root,'page-'+str(offset)+suffix,domain='industry_membership',
                source_profile='tushare.sw-pilot.index_member_all',source_profile_version=version,
                source_profile_digest=sw_digest(version),request={'endpoint':'index_member_all',
                    'params':{'is_new':'N','limit':'100','offset':str(offset)},
                    'fields':profile['endpoints']['index_member_all']['fields']},
                retrieved_at='2025-07-01T00:00:00Z',payload=_json_bytes(records),collector_code='fixture',summary=summary or {})
            return load_raw_batch(self.root,ref.raw_batch_id)
        first=page(0,100);last=page(100,1)
        self.assertFalse(validate_raw_completeness(first)['complete'])
        with self.assertRaisesRegex(SourceCompletenessError,'terminal page'):
            validate_raw_completeness(first,evidence=[first])
        self.assertTrue(validate_raw_completeness(first,evidence=[first,last])['complete'])
        with self.assertRaises(SourceCompletenessError):
            validate_raw_completeness(first,evidence=[last])
        for index,summary in enumerate(({'partial':True},{'truncated':True},{'complete':False})):
            incomplete=page(100,1,summary=summary,suffix='-incomplete-'+str(index))
            with self.assertRaisesRegex(SourceCompletenessError,'partial or truncated') as raised:
                validate_raw_completeness(first,evidence=[first,incomplete])
            self.assertEqual(raised.exception.raw_batch_id,incomplete.ref.raw_batch_id)
