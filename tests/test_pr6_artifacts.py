import json
import tempfile
import unittest
from pathlib import Path

from axiom_data import BuildApplication, MarketDomainBuilder, write_raw_batch, validate_domain_commit_closure
from axiom_data.artifacts import ArtifactError, _digest, _json_bytes
from axiom_data.pr6_source import Pr6Collector, Pr6Builder
from axiom_data.pit import select_revisions
from test_artifacts import security_row, write_rows, synthetic_source_fixture


class Client:
    def __init__(self, rows): self.rows=rows
    def query(self,*args,**kwargs): return self.rows


def income(value=100):
    return {'ts_code':'000001.SZ','ann_date':'20250401','f_ann_date':'20250401',
            'end_date':'20241231','report_type':'1','update_flag':'1',
            'revenue':value,'oper_cost':60,'n_income':10}


@synthetic_source_fixture
class Pr6ArtifactTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        raw=write_rows(self.root,'security-fixture','security_master',[security_row()],
                       retrieved_at='2025-01-01T00:00:00Z')
        self.security=BuildApplication('security_master',MarketDomainBuilder(self.root,'security_master')).build(
            None,[raw.raw_batch_id],[],'security_master.v1')

    def collect(self,value,when):
        return Pr6Collector(self.root,Client([income(value)])).collect('income',
            {'ts_code':'000001.SZ','period':'20241231','report_type':'1'},retrieved_at=when)

    def build(self,raw,parent=None):
        builder=Pr6Builder(self.root,'financial_events',dependency_commit_ids={'security_master':self.security.commit_id})
        ref=BuildApplication('financial_events',builder).build(parent,[raw.raw_batch_id],[],'financial_events.v2')
        return validate_domain_commit_closure(self.root,'financial_events',ref.commit_id)

    def test_raw_mapping_revision_retention_and_repeated_observation(self):
        first=self.build(self.collect(100,'2025-04-02T00:00:00Z'))
        repeated=self.build(self.collect(100,'2025-04-03T00:00:00Z'),first.ref.commit_id)
        self.assertEqual(first.rows[0]['revision_id'],repeated.rows[0]['revision_id'])
        self.assertEqual(len(repeated.rows[0]['observations']),2)
        changed=self.build(self.collect(None,'2025-06-02T00:00:00Z'),repeated.ref.commit_id)
        self.assertEqual(len(changed.rows),2)
        early=select_revisions(changed.rows,policy='operational_pit_v1',knowledge_cutoff='2025-05-01T00:00:00Z')
        self.assertEqual(early[0]['values']['revenue'],100)
        late=select_revisions(changed.rows,policy='operational_pit_v1',knowledge_cutoff='2025-07-01T00:00:00Z')
        self.assertIsNone(late[0]['values']['revenue'])
        self.assertEqual(late[0]['missing_reasons']['revenue'],'vendor_null')

    def test_financial_aba_and_clean_replay(self):
        raw=[self.collect(v,t) for v,t in [(100,'2025-04-02T00:00:00Z'),
             (200,'2025-04-04T00:00:00Z'),(100,'2025-04-06T00:00:00Z'),
             (100,'2025-04-08T00:00:00Z')]]
        commit=None
        for ref in raw:commit=self.build(ref,commit.ref.commit_id if commit else None)
        self.assertEqual(len(commit.rows),2)
        self.assertEqual(sum(len(r['observations']) for r in commit.rows),4)
        self.assertEqual(next(r for r in commit.rows if r['values']['revenue']==100)['first_observed_at'],'2025-04-02T00:00:00Z')
        builder=Pr6Builder(self.root,'financial_events',dependency_commit_ids={'security_master':self.security.commit_id})
        rebuilt=BuildApplication('financial_events',builder).build(None,[r.raw_batch_id for r in reversed(raw)],[],'financial_events.v2')
        replay=validate_domain_commit_closure(self.root,'financial_events',rebuilt.commit_id)
        self.assertEqual(commit.rows,replay.rows)
        import shutil
        with tempfile.TemporaryDirectory() as clean:
            shutil.copytree(self.root/'raw',Path(clean)/'raw')
            security=BuildApplication('security_master',MarketDomainBuilder(clean,'security_master')).build(None,['security-fixture'],[],'security_master.v1')
            fresh=BuildApplication('financial_events',Pr6Builder(clean,'financial_events',dependency_commit_ids={'security_master':security.commit_id})).build(None,[r.raw_batch_id for r in raw],[],'financial_events.v2')
            self.assertEqual(commit.rows,validate_domain_commit_closure(clean,'financial_events',fresh.commit_id).rows)
        for day,value in [('03',100),('05',200),('07',100),('09',100)]:
            selected=select_revisions(commit.rows,policy='operational_pit_v1',knowledge_cutoff='2025-04-'+day+'T00:00:00Z')
            self.assertEqual(selected[0]['values']['revenue'],value)
            self.assertIn('observation_id',selected[0]['observation_ref'])
        self.assertEqual(select_revisions(commit.rows,policy='best_effort_vendor_v1',knowledge_cutoff='2025-04-02T00:00:00Z')[0]['values']['revenue'],100)

    def test_incremental_exit_reentry_correction_and_replay(self):
        from axiom_data.pit import members
        def collect(start,end,days,observed):
            rows=[{'index_code':'000906.SH','con_code':'000001.SZ','trade_date':day,'weight':1} for day in days]
            return Pr6Collector(self.root,Client(rows)).collect('index_weight',
                {'index_code':'000906.SH','start_date':start,'end_date':end},retrieved_at=observed,
                membership_complete=True)
        first=collect('20250101','20250101',['20250101'],'2025-01-02T00:00:00Z')
        exit_raw=collect('20250601','20250601',[],'2025-06-02T00:00:00Z')
        enter=collect('20250801','20250801',['20250801'],'2025-08-02T00:00:00Z')
        correction=collect('20250715','20250801',['20250715'],'2025-09-02T00:00:00Z')
        app=BuildApplication('universe_membership',Pr6Builder(self.root,'universe_membership',
            dependency_commit_ids={'security_master':self.security.commit_id},
            builder_config={'membership_end_exclusive':None}))
        prior=None;states=[]
        for raw in (first,exit_raw,enter,correction):
            ref=app.build(prior,[raw.raw_batch_id],[],'universe_membership.v3')
            prior=ref.commit_id;states.append(validate_domain_commit_closure(self.root,'universe_membership',prior))
        self.assertIsNone(states[0].rows[0]['effective_to'])
        selected=select_revisions(states[1].rows,policy='operational_pit_v1',knowledge_cutoff='2025-06-03T00:00:00Z',group_states=states[1].manifest['group_states'])
        self.assertEqual([(r['effective_from'],r['effective_to']) for r in selected],[('2025-01-01','2025-06-01')])
        rebuilt=app.build(None,[r.raw_batch_id for r in (correction,first,enter,exit_raw)],[],'universe_membership.v3')
        self.assertEqual(states[-1].rows,validate_domain_commit_closure(self.root,'universe_membership',rebuilt.commit_id).rows)
        for policy in ('operational_pit_v1','best_effort_vendor_v1'):
            args=dict(policy=policy,knowledge_cutoff='2025-10-01T00:00:00Z',group_id='000906.SH')
            for day,count in [('2024-12-31',0),('2025-02-01',1),('2025-06-01',0),('2025-07-14',0),('2025-07-15',1),('2025-08-02',1)]:
                self.assertEqual(len(members(states[-1].rows,target_session=day,group_states=states[-1].manifest['group_states'],**args)),count)
        early=select_revisions(states[-1].rows,policy='operational_pit_v1',knowledge_cutoff='2025-01-03T00:00:00Z',group_states=states[-1].manifest['group_states'])
        self.assertIsNone(early[0]['effective_to'])

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
        old=app.build(None,[first.raw_batch_id],[],'universe_membership.v3')
        new=app.build(old.commit_id,[first.raw_batch_id,later.raw_batch_id],[],'universe_membership.v3')
        before=validate_domain_commit_closure(self.root,'universe_membership',old.commit_id)
        after=validate_domain_commit_closure(self.root,'universe_membership',new.commit_id)
        args={'policy':'operational_pit_v1','knowledge_cutoff':'2025-06-15T00:00:00Z'}
        self.assertEqual(select_revisions(before.rows,group_states=before.manifest['group_states'],**args),select_revisions(after.rows,group_states=after.manifest['group_states'],**args))
        changed=[r for r in after.rows if r['boundary_source_ref'] is not None]
        self.assertEqual(changed[0]['first_observed_at'],'2025-07-01T00:00:00Z')
        self.assertEqual(changed[0]['boundary_source_ref'],later.raw_batch_id)

    def test_valuation_requires_an_open_calendar_session(self):
        from test_artifacts import calendar_rows
        raw=write_rows(self.root,'calendar-fixture','trading_calendar',calendar_rows(),
                       retrieved_at='2026-01-01T00:00:00Z')
        cal=BuildApplication('trading_calendar',MarketDomainBuilder(self.root,'trading_calendar')).build(None,[raw.raw_batch_id],[],'trading_calendar.v1')
        source=Pr6Collector(self.root,Client([{'ts_code':'000001.SZ','trade_date':'20260103','pe':1,'pb':1,'ps':1}])).collect('daily_basic',{'ts_code':'000001.SZ','trade_date':'20260103'})
        builder=Pr6Builder(self.root,'valuation_daily',dependency_commit_ids={'security_master':self.security.commit_id,'trading_calendar':cal.commit_id})
        with self.assertRaisesRegex(ArtifactError,'open calendar'):
            BuildApplication('valuation_daily',builder).build(None,[source.raw_batch_id],[],'valuation_daily.v2')
