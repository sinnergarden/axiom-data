import copy
import tempfile
import unittest
from axiom_data import ArtifactError
from axiom_data.bootstrap_sources import plan_bootstrap_sources,collect_bootstrap_sources


class BootstrapSourceTest(unittest.TestCase):
    def plan(self):
        return plan_bootstrap_sources(symbols=['600036.SH'],start_session='2014-01-01',end_session='2014-02-03',
            financial_observation_start='2013-01-01',benchmarks=['000300.SH'],universe_ids=['000906.SH'])

    def test_explicit_scope_and_financial_lookback(self):
        plan=self.plan()
        self.assertEqual(len(plan['requests_by_domain'])+len(plan['required_reused_domains']),18)
        financial=plan['requests_by_domain']['financial_events']
        self.assertTrue(all(r['params']['start_date']=='20130101' for r in financial))
        self.assertTrue(all(r['params']['end_date']=='20140203' for requests in plan['requests_by_domain'].values()
                            for r in requests if r['endpoint'] not in {'index_weight','dividend'}))
        universe=plan['requests_by_domain']['universe_membership']
        self.assertEqual([(r['params']['start_date'],r['params']['end_date']) for r in universe],
                         [('20140101','20140131'),('20140201','20140203')])
        with self.assertRaises(ArtifactError):
            plan_bootstrap_sources(symbols=['600036.SH'],start_session='2014-01-01',end_session='2014-02-03',
                financial_observation_start='2015-01-01',benchmarks=['000300.SH'],universe_ids=['000906.SH'])

    def test_resume_validates_artifacts_and_does_not_accept_baseline(self):
        class Client:
            calls=0
            def query(self,*args,**kwargs):self.calls+=1;return []
        with tempfile.TemporaryDirectory() as root:
            client=Client();plan=self.plan()
            result=collect_bootstrap_sources(root,run_id='test',plan=plan,domains=['moneyflow_daily'],client=client)
            self.assertEqual(result['status'],'COMPLETE');self.assertFalse(result['ready_for_consumption'])
            again=collect_bootstrap_sources(root,run_id='test',plan=plan,domains=['moneyflow_daily'],client=client)
            self.assertEqual(result,again);self.assertEqual(client.calls,1)
            changed=copy.deepcopy(plan);changed['scope']['end_session']='2014-02-04'
            with self.assertRaisesRegex(ArtifactError,'plan changed'):
                collect_bootstrap_sources(root,run_id='test',plan=changed,domains=['moneyflow_daily'],client=client)

    def test_at_limit_financial_response_is_retained_but_not_admitted(self):
        class Client:
            def query(self,endpoint,*,fields,**params):
                return [dict(ts_code='600036.SH',ann_date='20130401',end_date='20121231',report_type='1')]*100
        with tempfile.TemporaryDirectory() as root:
            plan=self.plan();plan['requests_by_domain']['financial_events']=plan['requests_by_domain']['financial_events'][:1]
            result=collect_bootstrap_sources(root,run_id='test',plan=plan,domains=['financial_events'],client=Client())
            self.assertEqual(result['status'],'FAILED');self.assertEqual(result['failure']['kind'],'possible_truncation')
            from axiom_data import load_raw_batch
            self.assertEqual(load_raw_batch(root,result['failure']['raw_batch_id']).manifest['summary']['rows'],100)
