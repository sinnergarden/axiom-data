import json
import tempfile
import unittest
from pathlib import Path

from axiom_data import BuildApplication, MarketDomainBuilder, write_raw_batch, validate_domain_commit_closure
from axiom_data.artifacts import ArtifactError, _digest, _json_bytes
from axiom_data.pr6_source import Pr6Collector, Pr6Builder
from axiom_data.pit import select_revisions
from test_artifacts import security_row


class Client:
    def __init__(self, rows): self.rows=rows
    def query(self,*args,**kwargs): return self.rows


def income(value=100):
    return {'ts_code':'000001.SZ','ann_date':'20250401','f_ann_date':'20250401',
            'end_date':'20241231','report_type':'1','update_flag':'1',
            'revenue':value,'oper_cost':60,'n_income':10}


class Pr6ArtifactTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        raw=write_raw_batch(self.root,'security-fixture',domain='security_master',
            source_profile='fixture',source_profile_version='fixture.v1',
            source_profile_digest=_digest(b'fixture'),request={},
            retrieved_at='2025-01-01T00:00:00Z',payload=_json_bytes([security_row()]),
            collector_code='fixture',summary={})
        self.security=BuildApplication('security_master',MarketDomainBuilder(self.root,'security_master')).build(
            None,[raw.raw_batch_id],[],'security_master.v1')

    def collect(self,value,when):
        return Pr6Collector(self.root,Client([income(value)])).collect('income',
            {'ts_code':'000001.SZ','period':'20241231','report_type':'1'},retrieved_at=when)

    def build(self,raw,parent=None):
        builder=Pr6Builder(self.root,'financial_events',dependency_commit_ids={'security_master':self.security.commit_id})
        ref=BuildApplication('financial_events',builder).build(parent,[raw.raw_batch_id],[],'financial_events.v1')
        return validate_domain_commit_closure(self.root,'financial_events',ref.commit_id)

    def test_raw_mapping_revision_retention_and_repeated_observation(self):
        first=self.build(self.collect(100,'2025-04-02T00:00:00Z'))
        repeated=self.build(self.collect(100,'2025-04-03T00:00:00Z'),first.ref.commit_id)
        self.assertEqual(first.rows,repeated.rows)
        changed=self.build(self.collect(None,'2025-06-02T00:00:00Z'),repeated.ref.commit_id)
        self.assertEqual(len(changed.rows),2)
        early=select_revisions(changed.rows,policy='operational_pit_v1',knowledge_cutoff='2025-05-01T00:00:00Z')
        self.assertEqual(early[0]['values']['revenue'],100)
        late=select_revisions(changed.rows,policy='operational_pit_v1',knowledge_cutoff='2025-07-01T00:00:00Z')
        self.assertIsNone(late[0]['values']['revenue'])
        self.assertEqual(late[0]['missing_reasons']['revenue'],'vendor_null')

    def test_individual_batch_request_rejects_other_security_and_period(self):
        collector=Pr6Collector(self.root,Client([income()]))
        for params in ({'ts_code':'600000.SH','period':'20241231'},
                       {'ts_code':'000001.SZ','period':'20240930'}):
            with self.assertRaises(ArtifactError):collector.collect('income',params)

    def test_snapshot_security_dependency_is_enforced(self):
        row=income();row['ts_code']='600000.SH'
        raw=Pr6Collector(self.root,Client([row])).collect('income',{'ts_code':'600000.SH','period':'20241231'})
        with self.assertRaisesRegex(ArtifactError,'security identity'):self.build(raw)

    def test_builder_rejects_forged_per_batch_scope_and_profile(self):
        from axiom_data.artifacts import load_raw_batch
        raw=self.collect(100,'2025-04-02T00:00:00Z')
        frozen=load_raw_batch(self.root,raw.raw_batch_id)
        for name,digest,request in [
            ('wrong-profile',_digest(b'wrong'),frozen.manifest['request']),
            ('wrong-request',frozen.manifest['source_profile_digest'],
             dict(frozen.manifest['request'],params={'ts_code':'600000.SH','period':'20241231'}))]:
            forged=write_raw_batch(self.root,name,domain='financial_events',
                source_profile=frozen.manifest['source_profile_ref'],source_profile_version='tushare_pr6.v1',
                source_profile_digest=digest,request=request,retrieved_at='2025-04-02T00:00:00Z',
                payload=frozen.payload,collector_code='test',summary={})
            with self.assertRaises(ArtifactError):self.build(forged)
        self.assertFalse((self.root/'canonical/financial_events/commits').exists())

    def test_later_membership_boundary_does_not_rewrite_observed_prefix(self):
        def collect(day,when):
            rows=[{'index_code':'000906.SH','con_code':'000001.SZ','trade_date':day,'weight':1}]
            return Pr6Collector(self.root,Client(rows)).collect('index_weight',
                {'index_code':'000906.SH','start_date':day,'end_date':day},retrieved_at=when)
        first=collect('20250530','2025-05-31T00:00:00Z')
        later=collect('20250630','2025-07-01T00:00:00Z')
        app=BuildApplication('universe_membership',Pr6Builder(self.root,'universe_membership',
            dependency_commit_ids={'security_master':self.security.commit_id},
            builder_config={'membership_end_exclusive':'2025-08-01'}))
        old=app.build(None,[first.raw_batch_id],[],'universe_membership.v1')
        new=app.build(old.commit_id,[first.raw_batch_id,later.raw_batch_id],[],'universe_membership.v1')
        before=validate_domain_commit_closure(self.root,'universe_membership',old.commit_id)
        after=validate_domain_commit_closure(self.root,'universe_membership',new.commit_id)
        args={'policy':'operational_pit_v1','knowledge_cutoff':'2025-06-15T00:00:00Z'}
        self.assertEqual(select_revisions(before.rows,**args),select_revisions(after.rows,**args))
        changed=[r for r in after.rows if r['boundary_source_ref'] is not None]
        self.assertEqual(changed[0]['first_observed_at'],'2025-07-01T00:00:00Z')
        self.assertEqual(changed[0]['boundary_source_ref'],later.raw_batch_id)

    def test_valuation_requires_an_open_calendar_session(self):
        from test_artifacts import calendar_rows
        raw=write_raw_batch(self.root,'calendar-fixture',domain='trading_calendar',
            source_profile='fixture',source_profile_version='fixture.v1',source_profile_digest=_digest(b'fixture'),
            request={},retrieved_at='2026-01-01T00:00:00Z',payload=_json_bytes(calendar_rows()),collector_code='fixture',summary={})
        cal=BuildApplication('trading_calendar',MarketDomainBuilder(self.root,'trading_calendar')).build(None,[raw.raw_batch_id],[],'trading_calendar.v1')
        source=Pr6Collector(self.root,Client([{'ts_code':'000001.SZ','trade_date':'20260103','pe':1,'pb':1,'ps':1}])).collect('daily_basic',{'ts_code':'000001.SZ','trade_date':'20260103'})
        builder=Pr6Builder(self.root,'valuation_daily',dependency_commit_ids={'security_master':self.security.commit_id,'trading_calendar':cal.commit_id})
        with self.assertRaisesRegex(ArtifactError,'open calendar'):
            BuildApplication('valuation_daily',builder).build(None,[source.raw_batch_id],[],'valuation_daily.v1')
