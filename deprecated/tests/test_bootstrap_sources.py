import copy
import json
import tempfile
import unittest
from pathlib import Path
from axiom_data import ArtifactError
from axiom_data.bootstrap_sources import plan_bootstrap_sources,collect_bootstrap_sources


class BootstrapSourceTest(unittest.TestCase):
    def plan(self):
        return plan_bootstrap_sources(symbols=['600036.SH'],start_session='2014-01-01',end_session='2014-02-03',
            financial_observation_start='2013-01-01',benchmarks=['000300.SH'],universe_ids=['000906.SH'])

    def test_explicit_scope_and_financial_lookback(self):
        plan=self.plan()
        self.assertEqual({r['collector'] for requests in plan['requests_by_domain'].values()
                          for r in requests},
                         {'market','reference','fundamentals','financial_indicator','events',
                          'holder_reports_v3'})
        self.assertEqual(len(plan['requests_by_domain'])+len(plan['required_reused_domains']),18)
        financial=plan['requests_by_domain']['financial_events']
        self.assertTrue(all(r['params']['start_date']=='20130101' for r in financial if r['endpoint']!='fina_indicator'))
        self.assertTrue(all(r['params']['end_date']=='20140203' for requests in plan['requests_by_domain'].values()
                            for r in requests if r['endpoint'] not in {'index_weight','dividend','fina_indicator'}))
        indicator=[r['params'] for r in financial if r['endpoint']=='fina_indicator']
        self.assertEqual([(r['start_date'],r['end_date']) for r in indicator],
                         [('20130101','20130331'),('20130401','20130630'),('20130701','20130930'),
                          ('20131001','20131231'),('20140101','20140203')])
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
            legacy=copy.deepcopy(plan)
            legacy['requests_by_domain']['moneyflow_daily'][0]['collector']='events'
            result=collect_bootstrap_sources(root,run_id='test',plan=legacy,domains=['moneyflow_daily'],client=client)
            self.assertEqual(result['status'],'COMPLETE');self.assertFalse(result['ready_for_consumption'])
            again=collect_bootstrap_sources(root,run_id='test',plan=plan,domains=['moneyflow_daily'],client=client)
            self.assertEqual(result,again);self.assertEqual(client.calls,1)
            frozen=json.loads((Path(root)/'operations/test/source_plan.json').read_bytes())
            self.assertEqual(frozen['plan']['requests_by_domain']['moneyflow_daily'][0]['collector'],'events')
            changed=copy.deepcopy(plan);changed['scope']['end_session']='2014-02-04'
            with self.assertRaisesRegex(ArtifactError,'plan changed'):
                collect_bootstrap_sources(root,run_id='test',plan=changed,domains=['moneyflow_daily'],client=client)

    def test_at_limit_financial_response_is_retained_but_not_admitted(self):
        class Client:
            def query(self,endpoint,*,fields,**params):
                return [dict(ts_code='600036.SH',ann_date='20130401',end_date=params['end_date'])]*100
        with tempfile.TemporaryDirectory() as root:
            plan=self.plan();plan['requests_by_domain']['financial_events']=[next(
                r for r in plan['requests_by_domain']['financial_events'] if r['endpoint']=='fina_indicator')]
            result=collect_bootstrap_sources(root,run_id='test',plan=plan,domains=['financial_events'],client=Client())
            self.assertEqual(result['status'],'FAILED');self.assertEqual(result['failure']['kind'],'possible_truncation')
            from axiom_data import load_raw_batch
            self.assertEqual(load_raw_batch(root,result['failure']['raw_batch_id']).manifest['summary']['rows'],100)
            self.assertEqual(result['failure']['split_status'],'UNSPLITTABLE_SOURCE_SCOPE')
            self.assertTrue(result['domains']['financial_events']['splits'])
            self.assertEqual(result['domains']['financial_events']['completed_raw_batch_ids'],[])

    def test_automatic_bounded_split_completes_and_resume_reuses_every_raw(self):
        class Client:
            calls=0
            def query(self,endpoint,*,fields,**params):
                self.calls+=1
                count=100 if params['start_date']=='20130101' and params['end_date']=='20130331' else 1
                return [dict(ts_code='600036.SH',ann_date='20130401',end_date=params['end_date'])]*count
        from axiom_data import load_raw_batch
        with tempfile.TemporaryDirectory() as root:
            plan=self.plan();plan['requests_by_domain']['financial_events']=[next(
                r for r in plan['requests_by_domain']['financial_events'] if r['endpoint']=='fina_indicator')]
            client=Client()
            result=collect_bootstrap_sources(root,run_id='split',plan=plan,domains=['financial_events'],client=client)
            self.assertEqual(result['status'],'COMPLETE');self.assertEqual(client.calls,3)
            domain=result['domains']['financial_events'];parent=domain['splits'][0]['parent_raw_batch_id']
            self.assertEqual(load_raw_batch(root,parent).manifest['summary']['rows'],100)
            self.assertNotIn(parent,domain['completed_raw_batch_ids'])
            self.assertEqual(len(domain['completed_raw_batch_ids']),2)
            self.assertEqual(result['validated_rows'],2)
            again=collect_bootstrap_sources(root,run_id='split',plan=plan,domains=['financial_events'],client=client)
            self.assertEqual(again,result);self.assertEqual(client.calls,3)

    def test_resume_continues_failed_child_without_refetching_parent_or_complete_child(self):
        class Client:
            calls=[];fail=True
            def query(self,endpoint,*,fields,**params):
                scope=(params['start_date'],params['end_date']);self.calls.append(scope)
                if scope==('20130215','20130331') and self.fail:raise ConnectionError('offline')
                count=100 if scope==('20130101','20130331') else 1
                return [dict(ts_code='600036.SH',ann_date='20130401',end_date=params['end_date'])]*count
        with tempfile.TemporaryDirectory() as root:
            plan=self.plan();plan['requests_by_domain']['financial_events']=[next(
                r for r in plan['requests_by_domain']['financial_events'] if r['endpoint']=='fina_indicator')]
            client=Client()
            first=collect_bootstrap_sources(root,run_id='split',plan=plan,domains=['financial_events'],client=client)
            self.assertEqual(first['status'],'FAILED');self.assertEqual(len(client.calls),3)
            client.fail=False
            final=collect_bootstrap_sources(root,run_id='split',plan=plan,domains=['financial_events'],client=client)
            self.assertEqual(final['status'],'COMPLETE');self.assertEqual(len(client.calls),4)
            self.assertEqual(client.calls.count(('20130101','20130331')),1)
