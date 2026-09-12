import copy
import tempfile
import unittest

from axiom_data import ArtifactError
from axiom_data.historical_sparse import (plan_historical_sparse, execute_historical_sparse,
                                          validate_sparse_coverage, _check_children)


class HistoricalSparseTest(unittest.TestCase):
    def plan(self):
        return plan_historical_sparse(symbols=['600036.SH'], start_session='2014-01-01',
                                      end_session='2026-09-01')

    def test_plan_has_large_scopes_and_reference_history(self):
        plan = self.plan()
        self.assertEqual(plan, self.plan())
        requests = plan['requests_by_domain']
        self.assertEqual(len(requests['forecast_observations']), 1)
        self.assertEqual(len(requests['corporate_actions']), 1)
        self.assertEqual({s['params']['is_new'] for s in requests['industry_membership']}, {'Y','N'})
        self.assertEqual({s['params']['list_status'] for s in requests['security_master']}, {'L','D','P'})
        self.assertLess(sum(map(len, requests.values())), 20)

    def test_empty_coverage_is_durable_and_resume_does_not_recollect(self):
        class Client:
            calls = 0
            def query(self, *args, **kwargs):
                self.calls += 1
                return []
        with tempfile.TemporaryDirectory() as root:
            plan = self.plan(); domains = ['forecast_observations']; client = Client()
            missing = validate_sparse_coverage(root, run_id='sparse', plan=plan, domains=domains)
            self.assertEqual(missing['coverage'][0]['status'], 'NOT_QUERIED')
            result = execute_historical_sparse(root, run_id='sparse', plan=plan, domains=domains, client=client)
            self.assertTrue(result['complete'])
            self.assertTrue(result['coverage'][0]['empty'])
            self.assertTrue(result['coverage'][0]['observations'][0]['empty_result'])
            self.assertEqual(execute_historical_sparse(root, run_id='sparse', plan=plan,
                domains=domains, client=client), result)
            self.assertEqual(client.calls, 1)

    def test_failure_does_not_assert_empty(self):
        class Client:
            def query(self, *args, **kwargs):
                raise ConnectionError('offline')
        with tempfile.TemporaryDirectory() as root:
            result = execute_historical_sparse(root, run_id='fail', plan=self.plan(),
                domains=['forecast_observations'], client=Client())
            self.assertFalse(result['complete'])
            self.assertEqual(result['coverage'][0]['status'], 'COLLECTION_FAILURE')
            self.assertIsNone(result['coverage'][0]['empty'])

    def test_child_partition_rejects_gap_overlap_and_selector_change(self):
        parent = self.plan()['requests_by_domain']['forecast_observations'][0]
        left = copy.deepcopy(parent); right = copy.deepcopy(parent)
        left['params']['end_date'] = '20200101'
        right['params']['start_date'] = '20200102'
        _check_children(parent, [left, right])
        for start in ('20200101', '20200103'):
            bad = copy.deepcopy(right); bad['params']['start_date'] = start
            with self.assertRaises(ArtifactError):
                _check_children(parent, [left, bad])
        bad = copy.deepcopy(right); bad['params']['ts_code'] = '000001.SZ'
        with self.assertRaises(ArtifactError):
            _check_children(parent, [left, bad])

    def test_split_aggregation_retains_parent_and_revalidates_children(self):
        from axiom_data.source_completeness import completeness_policy
        cap = completeness_policy('tushare_pr7.v1', 'forecast')['limit']
        class Client:
            calls = 0
            def query(self, endpoint, **kwargs):
                self.calls += 1
                if kwargs['start_date'] == '20140101' and kwargs['end_date'] == '20260901':
                    return [dict(ts_code='600036.SH', ann_date='20140101', end_date='20140331', type='预增')] * cap
                return []
        with tempfile.TemporaryDirectory() as root:
            plan = self.plan(); client = Client(); domains = ['forecast_observations']
            result = execute_historical_sparse(root, run_id='split', plan=plan, domains=domains, client=client)
            self.assertTrue(result['complete'])
            parent = result['coverage'][0]
            self.assertEqual(len(parent['children']), 2)
            self.assertTrue(parent['empty'])
            self.assertEqual(len(parent['observations']), 2)
            self.assertEqual(client.calls, 3)
            self.assertEqual(execute_historical_sparse(root, run_id='split', plan=plan,
                domains=domains, client=client), result)
            self.assertEqual(client.calls, 3)

    def test_unknown_or_invalid_cap_cannot_create_split(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from axiom_data.bootstrap_sources import plan_truncated_raw_split
        raw = SimpleNamespace(manifest={'source_profile_version': 'tushare_pr7.v1',
            'status': 'success', 'request': {'endpoint': 'forecast',
            'params': {'ts_code': '600036.SH', 'start_date': '20140101', 'end_date': '20260101'}}}, payload=b'[]')
        for cap in (None, True, 0, -1, '100'):
            with patch('axiom_data.source_completeness.completeness_policy', return_value={
                    'status': 'established', 'action': 'split_date_scope', 'limit': cap}):
                with self.assertRaises(ArtifactError):
                    plan_truncated_raw_split(raw)

    def test_bounded_requested_interval_is_distinct_from_economic_target(self):
        with tempfile.TemporaryDirectory() as root:
            plan = self.plan()
            result = validate_sparse_coverage(root, run_id='unqueried', plan=plan,
                domains=['top_holders_reports', 'corporate_actions'])
            holder = result['coverage'][0]
            self.assertEqual(holder['requested_interval'], {'start': '20140101', 'end': '20181231'})
            self.assertEqual(holder['economic_target_interval'], {'start': '20140101', 'end': '20260901'})
            dividend = result['coverage'][-1]
            self.assertIsNone(dividend['requested_interval'])
