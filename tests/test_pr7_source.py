import copy
import tempfile
import unittest
from pathlib import Path
from axiom_data import BuildApplication,MarketDomainBuilder,write_raw_batch,validate_domain_commit_closure
from axiom_data.artifacts import _digest,_json_bytes,load_raw_batch,ArtifactError
from axiom_data.pr7_source import Pr7Collector,Pr7Builder,normalize,validate_payload
from axiom_data.domains.pr7 import validate_rows
from axiom_data.pit import select_revisions
from test_artifacts import security_row, write_rows, synthetic_source_fixture
from test_pr6_artifacts import Client

PARAMS={'ts_code':'000001.SZ','start_date':'20240101','end_date':'20260101'}
def holders(count=10):
    return [dict(ts_code='000001.SZ',ann_date='20250401',end_date='20241231',holder_name='holder'+str(i),holder_type='institution',hold_amount=100+i,hold_ratio=1+i/100) for i in range(count)]


def current_pr7_snapshot(root, snapshot_id, domains):
    """Rebuild only requested PR7 domains from their immutable full Raw closure."""
    from axiom_data import SnapshotReader, create_snapshot
    from axiom_data.artifacts import _DOMAIN_DEPENDENCIES, _validated_domain_commit_with_raw_closure
    reader=SnapshotReader(root,snapshot_id)
    ids={domain:commit.ref.commit_id for domain,commit in reader.commits.items()}
    for domain in domains:
        old,raw_ids=_validated_domain_commit_with_raw_closure(root,domain,ids[domain])
        builder=Pr7Builder(root,domain,builder_config=old.manifest['builder_config'],
            dependency_commit_ids={dependency:ids[dependency] for dependency in _DOMAIN_DEPENDENCIES[domain]})
        ref=BuildApplication(domain,builder).build(None,sorted(raw_ids),[],old.ref.contract_version)
        current=validate_domain_commit_closure(root,domain,ref.commit_id)
        if current.rows != old.rows:
            raise AssertionError('current fixture lineage changed canonical values')
        ids[domain]=ref.commit_id
    return create_snapshot(root,ids).snapshot_id

@synthetic_source_fixture
class Pr7SourceTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        r=write_rows(self.root,'security-fixture','security_master',[security_row()], retrieved_at='2025-01-01T00:00:00Z')
        self.security=BuildApplication('security_master',MarketDomainBuilder(self.root,'security_master')).build(None,[r.raw_batch_id],[],'security_master.v1')
    def raw(self,endpoint,rows,when='2025-04-02T00:00:00Z'):
        ref=Pr7Collector(self.root,Client(rows)).collect(endpoint,PARAMS,retrieved_at=when)
        return load_raw_batch(self.root,ref.raw_batch_id)
    def build(self,raws,domain='top_holders_reports',parent=None):
        config = {'forecast_source_types':'forecast_source_types.v1'} if domain == 'forecast_observations' else {}
        b=Pr7Builder(self.root,domain,dependency_commit_ids={'security_master':self.security.commit_id}, builder_config=config)
        version = 'forecast_observations.v2' if domain == 'forecast_observations' else domain+'.v1'
        ref=BuildApplication(domain,b).build(parent,[r.ref.raw_batch_id for r in raws],[],version)
        return validate_domain_commit_closure(self.root,domain,ref.commit_id)
    def select(self,c,t='2025-09-01T00:00:00Z'):
        return select_revisions(c.rows,policy='operational_pit_v1',knowledge_cutoff=t)
    def test_full_incomplete_revision_null_holder_and_reentry(self):
        a=self.raw('top10_holders',holders());c1=self.build([a])
        self.assertEqual(len(c1.rows[0]['holders']),10)
        b=self.raw('top10_holders',holders(8),'2025-04-03T00:00:00Z');c2=self.build([b],parent=c1.ref.commit_id)
        selected=self.select(c2)[0];self.assertEqual(len(selected['holders']),8)
        self.assertIsNone(selected['values']['top10_ratio']);self.assertEqual(selected['missing_reasons']['top10_ratio'],'incomplete_report')
        self.assertEqual(self.select(c1,'2025-04-02T12:00:00Z'),self.select(c2,'2025-04-02T12:00:00Z'))
        rows=holders();rows[-1]['hold_ratio']=None
        c=self.raw('top10_holders',rows,'2025-04-04T00:00:00Z');c3=self.build([c],parent=c2.ref.commit_id)
        self.assertIsNone(self.select(c3)[0]['values']['top10_ratio'])
        d=self.raw('top10_holders',holders(),'2025-04-05T00:00:00Z');c4=self.build([d],parent=c3.ref.commit_id)
        self.assertAlmostEqual(self.select(c4)[0]['values']['top10_ratio'],10.45)
        clean=self.build([a,b,c,d]);self.assertEqual(c4.rows,clean.rows)
        self.assertEqual(len(c4.rows),3)
    def test_holder_count_revision_and_zero(self):
        rows=[dict(ts_code='000001.SZ',ann_date='20250401',end_date='20241231',holder_num=100)]
        a=self.raw('stk_holdernumber',rows);c=self.build([a],'holder_count_events')
        rows[0]['holder_num']=0;b=self.raw('stk_holdernumber',rows,'2025-04-03T00:00:00Z')
        c=self.build([b],'holder_count_events',c.ref.commit_id)
        self.assertEqual(self.select(c)[0]['values']['number'],0)
    def test_moneyflow_independent_units_and_sign(self):
        r=self.raw('moneyflow',[dict(ts_code='000001.SZ',trade_date='20250610',buy_elg_amount=3,sell_elg_amount=7,net_mf_amount=-9)])
        row=normalize(r,'moneyflow_daily')[0]
        self.assertEqual(row['values'],{'big_buy':30000,'big_sell':70000,'net':-90000})
    def test_margin_zero_and_missing_distinct(self):
        r=self.raw('margin_detail',[dict(ts_code='000001.SZ',trade_date='20250610',rzye=0,rzmre=None,rqyl=10,rqchl=2,rqmcl=1,rzrqye=3)])
        row=normalize(r,'margin_daily')[0]
        self.assertEqual(row['values']['balance'],0);self.assertEqual(row['values']['lend_balance_volume'],10)
        self.assertEqual(row['missing_reasons'],{'buy':'vendor_null','repay':'not_provided'})
    def test_forecast_publication_revision_prefix(self):
        rows=[dict(ts_code='000001.SZ',ann_date='20250401',end_date='20251231',type='预增')]
        a=self.raw('forecast',rows);c1=self.build([a],'forecast_observations')
        rows[0]['type']='预减';b=self.raw('forecast',rows,'2025-04-04T00:00:00Z');c2=self.build([b],'forecast_observations',c1.ref.commit_id)
        self.assertEqual(self.select(c1,'2025-04-03T00:00:00Z'),self.select(c2,'2025-04-03T00:00:00Z'))
        self.assertEqual(self.select(c2)[0]['values']['type'],'预减')
    def test_source_gap_does_not_create_empty_report(self):
        self.assertEqual(normalize(self.raw('top10_holders',[]),'top_holders_reports'),[])
    def test_wrong_request_and_duplicate_holder_rejected(self):
        rows=holders();rows[0]['ts_code']='600000.SH'
        with self.assertRaises(ArtifactError):self.raw('top10_holders',rows)
        rows=holders();rows.append(rows[0])
        with self.assertRaises(ArtifactError):normalize(self.raw('top10_holders',rows),'top_holders_reports')
    def test_missing_holder_corruption_rejected(self):
        c=self.build([self.raw('top10_holders',holders())]);rows=copy.deepcopy(list(c.rows));rows[0]['holders'].pop()
        with self.assertRaises(ValueError):validate_rows('top_holders_reports',rows)
    def test_verified_fail_closed(self):
        c=self.build([self.raw('top10_holders',holders())]);rows=copy.deepcopy(list(c.rows));rows[0]['pit_qualification']='verified'
        with self.assertRaisesRegex(ValueError,'VERIFIED'):select_revisions(rows,policy='operational_pit_v1',knowledge_cutoff='2026-01-01T00:00:00Z')

    def test_raw_scope_limit_and_unknown_profile(self):
        with self.assertRaises(ArtifactError):validate_payload('moneyflow',{'ts_code':'000001.SZ'},[])
        with self.assertRaises(ArtifactError):validate_payload('other',PARAMS,[])
        with self.assertRaises(ArtifactError):validate_payload('moneyflow',PARAMS,[{}]*6000)

    def test_same_publication_conflict_is_rejected(self):
        rows=[dict(ts_code='000001.SZ',ann_date='20250401',end_date='20241231',holder_num=n) for n in (1,2)]
        with self.assertRaises(ArtifactError):self.build([self.raw('stk_holdernumber',rows)],'holder_count_events')

    def test_forged_canonical_mapping_fails_closed(self):
        raw=self.raw('top10_holders',holders())
        class Forged(Pr7Builder):
            def _build_rows(self,*args):
                rows=super()._build_rows(*args);rows[0]['values']['top10_ratio']=1
                return rows
        builder=Forged(self.root,'top_holders_reports',dependency_commit_ids={'security_master':self.security.commit_id})
        with self.assertRaisesRegex(ArtifactError,'RawBatch mapping'):
            BuildApplication('top_holders_reports',builder).build(None,[raw.ref.raw_batch_id],[],'top_holders_reports.v1')

    def test_different_periods_remain_separate(self):
        rows=holders()+[dict(r,end_date='20250331',ann_date='20250501') for r in holders()]
        c=self.build([self.raw('top10_holders',rows)])
        self.assertEqual({r['report_period'] for r in self.select(c)}, {'2024-12-31','2025-03-31'})

    def test_same_retrieval_publication_order_and_visible_world(self):
        from axiom_data.pr7_source import select_pr7_revisions
        rows=[dict(ts_code='000001.SZ',end_date='20241231',ann_date=ann,holder_num=n) for ann,n in [('20250401',100),('20250501',200)]]
        c=self.build([self.raw('stk_holdernumber',rows,'2025-06-01T00:00:00Z')],'holder_count_events')
        early=select_pr7_revisions(c.rows,policy='best_effort_vendor_v1',knowledge_cutoff='2025-04-15T00:00:00Z')
        self.assertEqual(early[0]['values']['number'],100)
        late=select_pr7_revisions(c.rows,policy='operational_pit_v1',knowledge_cutoff='2025-06-02T00:00:00Z')
        self.assertEqual(late[0]['values']['number'],200)
        self.assertEqual(select_pr7_revisions(c.rows,policy='operational_pit_v1',knowledge_cutoff='2025-05-30T00:00:00Z'),())
