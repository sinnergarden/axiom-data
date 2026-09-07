import tempfile
import unittest
from pathlib import Path
from axiom_data import BuildApplication, create_snapshot, SnapshotReader, FactView
from axiom_data.domains import DM1_SNAPSHOT_DOMAINS
from axiom_data.artifacts import _DOMAIN_DEPENDENCIES
from axiom_data.pr6_source import Pr6Collector,Pr6Builder
from axiom_data.pr6_views import build_pr6_fact_view
from axiom_data.consumption import QlibViewReader
from test_pr5_dm1 import build_all,collect_all
from test_pr6_artifacts import Client


class Pr6IntegrationTest(unittest.TestCase):
    def test_snapshot_read_union_cohort_industry_and_qlib(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);baseline=build_all(root,collect_all(root))
            commits={d:baseline[d] for d in DM1_SNAPSHOT_DOMAINS}
            requests={
              'universe_membership': [('index_weight',{'index_code':'000906.SH','start_date':'20260101','end_date':'20260107'},[
                {'index_code':'000906.SH','con_code':symbol,'trade_date':day,'weight':1}
                for day,symbol in [('20260101','600002.SH'),('20260105','600000.SH'),('20260106','600002.SH'),('20260107','600000.SH')]])],
              'industry_membership': [('bak_basic',{'ts_code':'600000.SH','trade_date':day},[
                {'ts_code':'600000.SH','trade_date':day,'industry':industry}]) for day,industry in [('20260105','bank'),('20260107','industry')]],
              'financial_events': [('income',{'ts_code':'600000.SH','period':'20250331'},[
                {'ts_code':'600000.SH','ann_date':'20250401','f_ann_date':'20250401','end_date':'20250331','report_type':'1','update_flag':'1','revenue':100,'oper_cost':60,'n_income':10}])],
              'valuation_daily': [('daily_basic',{'ts_code':'600000.SH','trade_date':'20260105'},[
                {'ts_code':'600000.SH','trade_date':'20260105','pe':10,'pb':1,'ps':2}])],
            }
            requests['financial_events'].append(('fina_indicator',{'ts_code':'600000.SH','period':'20250331'},[{'ts_code':'600000.SH','ann_date':'20250401','end_date':'20250331','update_flag':'1','current_ratio':None,'debt_to_assets':None,'grossprofit_margin':None,'roe':None}]))
            for domain,jobs in requests.items():
                refs=[Pr6Collector(root,Client(rows)).collect(endpoint,params,retrieved_at='2026-09-01T00:00:00Z').raw_batch_id for endpoint,params,rows in jobs]
                config={'membership_end_exclusive':'2026-01-08'} if domain=='universe_membership' else {}
                builder=Pr6Builder(root,domain,builder_config=config,dependency_commit_ids={d:commits[d] for d in _DOMAIN_DEPENDENCIES[domain]})
                commits[domain]=BuildApplication(domain,builder).build(None,refs,[],domain+'.v1').commit_id
            snapshot=create_snapshot(root,commits);reader=SnapshotReader(root,snapshot.snapshot_id)
            args={'pit_policy':'best_effort_vendor_v1','knowledge_cutoff':'2026-09-02T00:00:00Z'}
            union=reader.historical_union('000906.SH','2026-01-05','2026-01-07','2026-01-02',**args)
            self.assertIn('600000.SH',union)
            history=reader.market_daily(['600000.SH'],'2026-01-02','2026-01-05')
            self.assertEqual(history[0]['session'],'2026-01-02')
            self.assertNotIn('600000.SH',[r['symbol'] for r in reader.members('000906.SH','2026-01-02',**args)])
            for day,expected in [('2026-01-05',True),('2026-01-06',False),('2026-01-07',True)]:
                self.assertEqual('600000.SH' in [r['symbol'] for r in reader.members('000906.SH',day,**args)],expected)
            for day,expected in [('2026-01-05','bank'),('2026-01-07','industry')]:
                self.assertEqual(reader.members('tushare_bak_basic',day,domain='industry_membership',**args)[0]['industry_id'],expected)
            view=build_pr6_fact_view(root,snapshot.snapshot_id,symbols=['600000.SH'],start_session='2026-01-02',end_session='2026-01-05',universe_ids=['000906.SH'],industry_system='tushare_bak_basic',**args)
            direct=FactView(root,snapshot.snapshot_id,pr6_fact_view_id=view.view_id).read('pr6')['rows']
            binary=QlibViewReader(root,view.view_id).market_daily(include_missing=True)
            self.assertEqual(direct,binary)
            self.assertEqual(direct[-1]['financial.single_quarter_revenue'],100)
            # Available income/cost do not silently replace a missing supplier margin.
            self.assertIsNone(direct[-1]['indicator.gross_margin'])
