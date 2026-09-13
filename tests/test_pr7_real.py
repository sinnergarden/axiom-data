"""Regression against the explicitly reported bounded supplier closure."""
import copy
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from axiom_data import SnapshotReader,FactView
from axiom_data.artifacts import ArtifactError
from axiom_data.pr7_source import Pr7Collector,extend_snapshot
from axiom_data.pr7_views import load_pr7_fact_view,build_pr7_fact_view
from axiom_data.consumption import QlibViewReader
from axiom_data.offline_guard import deny_external_data
from test_pr6_artifacts import Client
from test_pr7_source import current_pr7_snapshot

REPORT=Path(__file__).resolve().parents[1]/'reports/pr7/run_manifest.json'

@unittest.skipUnless(REPORT.exists(),'bounded real closure must be built explicitly')
class Pr7RealTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        report=json.loads(REPORT.read_text());cls.root=Path(report['source_root']);cls.refs=report['refs']
        cls.reader=SnapshotReader(cls.root,cls.refs['snapshot_id'])
    def query(self,leaf,symbol='688981.SH',**kwargs):
        return self.reader.leaf_fact(leaf,symbol=symbol,target_session=kwargs.pop('target_session','2025-06-13'),knowledge_cutoff=kwargs.pop('knowledge_cutoff','2025-06-13T23:59:59+08:00'),pit_policy=kwargs.pop('pit_policy','best_effort_vendor_v1'),**kwargs)
    def test_real_holder_group_and_forecast(self):
        h=self.query('holder.top10_ratio');self.assertEqual(len(h['holders']),10);self.assertEqual(h['quality'],'complete')
        self.assertIsNotNone(h['derived_ref']);self.assertEqual(len(h['component_refs']),10)
        f=self.query('forecast.type','000401.SZ');self.assertIsNotNone(f['value']);self.assertEqual(f['source_kind'],'company_performance_forecast')
        self.assertIsNone(self.query('forecast.type','688981.SH')['value'])
    def test_operational_before_actual_observation_is_missing(self):
        for leaf in ('holder.number','holder.top10_ratio','margin.balance','moneyflow.net','forecast.type'):
            self.assertEqual(self.query(leaf,pit_policy='operational_pit_v1')['missing_reason'],'no_observation_at_cutoff')
    def test_insufficient_and_unknown_scope_fail(self):
        with self.assertRaisesRegex(ArtifactError,'unknown security'):self.query('holder.number','999999.SH')
        with self.assertRaisesRegex(ArtifactError,'INSUFFICIENT_SCOPE'):self.query('margin.balance',target_session='2025-06-16')
        with self.assertRaises(ArtifactError):self.reader.as_of('margin_daily',symbols=['999999.SH'],knowledge_cutoff='2025-06-13T23:59:59+08:00',pit_policy='best_effort_vendor_v1')
    def test_qlib_numeric_and_typed_metadata(self):
        view=load_pr7_fact_view(self.root,self.refs['pr7_view_id']);q=QlibViewReader(self.root,self.refs['pr7_view_id'])
        direct={(r['symbol'],r['session']):r for r in view.rows}
        for r in q.market_daily(include_missing=True):
            for field,value in direct[(r['symbol'],r['session'])]['values'].items():
                if value is None:self.assertIsNone(r[field])
                else:self.assertTrue(math.isclose(value,r[field],rel_tol=1e-6,abs_tol=1e-6))
        self.assertEqual(len(q.fact_metadata()['rows']),12)
    def test_legacy_guard_is_enforced_and_reads_work(self):
        with deny_external_data(self.root) as attempts:
            with self.assertRaises(PermissionError):Path('/tmp/SysQ/data/source.parquet').read_bytes()
            attempts.clear();self.assertIsNotNone(self.query('margin.balance')['value']);self.assertEqual(attempts,[])
    def test_shareholder_only_extension_preserves_other_domains_and_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'root';shutil.copytree(self.root,root)
            parent_id=current_pr7_snapshot(root,self.refs['snapshot_id'],['holder_count_events'])
            before=SnapshotReader(root,parent_id)
            visible=before.as_of('holder_count_events',symbols=['688981.SH'],pit_policy='best_effort_vendor_v1',knowledge_cutoff='2025-06-13T23:59:59+08:00')
            old=max(visible,key=lambda r:r['report_period'])
            source={'ts_code':old['symbol'],'ann_date':old['announcement'].replace('-',''),'end_date':old['report_period'].replace('-',''),'holder_num':1}
            raw=Pr7Collector(root,Client([source])).collect('stk_holdernumber',{'ts_code':'688981.SH','start_date':'20240101','end_date':'20250613'},retrieved_at='2026-09-10T00:00:00Z')
            with patch.object(Pr7Collector,'collect',side_effect=AssertionError('extension must not recollect')):
                snapshot=extend_snapshot(root,parent_id,'holder_count_events',[raw.raw_batch_id])
            after=SnapshotReader(root,snapshot.snapshot_id)
            for domain,c in before.commits.items():
                if domain!='holder_count_events':self.assertEqual(c.ref.commit_id,after.commits[domain].ref.commit_id)
            query=dict(symbols=['688981.SH'],pit_policy='operational_pit_v1',knowledge_cutoff='2026-09-09T23:59:59+08:00')
            self.assertEqual(before.as_of('holder_count_events',**query),after.as_of('holder_count_events',**query))
            newer=after.as_of('holder_count_events',symbols=['688981.SH'],pit_policy='operational_pit_v1',knowledge_cutoff='2026-09-11T00:00:00Z')
            self.assertEqual(next(r for r in newer if r['report_period']==old['report_period'])['values']['number'],1)
    def test_view_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'root';shutil.copytree(self.root,root)
            from axiom_data.artifacts import _layout
            path=_layout(root).derived_commits('pr7_fact')/self.refs['pr7_view_id']/'rows.json'
            data=json.loads(path.read_text());data['wide'][0]['values']['holder.number']=1;path.write_text(json.dumps(data))
            with self.assertRaises(ArtifactError):load_pr7_fact_view(root,self.refs['pr7_view_id'])
    def test_complete_subset_membership_regression(self):
        query=dict(pit_policy='best_effort_vendor_v1',knowledge_cutoff='2025-06-13T23:59:59+08:00')
        all_rows=self.reader.as_of('universe_membership',**query)
        subset=self.reader.as_of('universe_membership',symbols=['688981.SH'],**query)
        self.assertEqual(len(all_rows),12);self.assertEqual(len(subset),2)
        self.assertEqual(subset,tuple(r for r in all_rows if r['symbol']=='688981.SH'))

    def test_published_pr7_v1_view_still_loads(self):
        old=load_pr7_fact_view(Path('/home/liuming/workspace/axiom/data/forensic/pr7-dm2-20260909-r1/admission-r3/source'),'pr7-fact-bbd7326584cd74667cc3de6d964ee10cbd4e2b434cffcd94222ecee7a2aedcae')
        self.assertEqual(old.manifest['schema_version'],'pr7_fact_view.v1')
        self.assertEqual(len(old.rows),12)
