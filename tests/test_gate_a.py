import copy
import unittest
from unittest.mock import patch

from axiom_data import ArtifactError
from axiom_data import bootstrap_sources, gate_a, source_completeness


class GateATest(unittest.TestCase):
    def setUp(self):
        self.scope = dict(symbols=['600036.SH', '000001.SZ'],
                          start_session='2014-01-01', end_session='2014-02-03',
                          financial_observation_start='2013-01-01',
                          benchmarks=['000300.SH'], universe_ids=['000906.SH'])
        self.plan = gate_a.make_gate_a_plan(self.scope)

    def test_current_real_readiness_is_blocked_without_claiming_future_results(self):
        result = gate_a.validate_gate_a(self.plan)
        self.assertEqual(result['status'], 'GATE_A_BLOCKED')
        self.assertEqual(len(result['evidence']['requirement_bindings']), 56)
        self.assertEqual(result['evidence']['source_plan']['scope'], self.scope)
        self.assertTrue(any(f['section'] == 'terminal_plan' for f in result['findings']))
        self.assertTrue(result['external_review_required'])
        self.assertFalse(result['bulk_authorized'])
        self.assertFalse(result['ready_for_consumption'])
        self.assertEqual(result['gate_b_status'], 'NOT_ASSESSED')

    def test_missing_required_profile_blocks_its_real_source(self):
        real = gate_a._profile
        def missing(version):
            if version == 'tushare_fina_indicator.v1':
                raise FileNotFoundError(version)
            return real(version)
        with patch.object(gate_a, '_profile', side_effect=missing):
            result = gate_a.validate_gate_a(self.plan)
        self.assertTrue(any(f['section'] == 'source:financial_events:tushare_fina_indicator.v1'
                            and f['error_type'] == 'FileNotFoundError' for f in result['findings']))

    def test_missing_planner_and_internal_window_hole_block(self):
        with patch.object(bootstrap_sources, 'plan_bootstrap_sources', None):
            result = gate_a.validate_gate_a(self.plan)
        self.assertTrue(any(f['section'] == 'source_plan' for f in result['findings']))
        real = bootstrap_sources.plan_bootstrap_sources
        def gap(**scope):
            plan = real(**scope)
            first = plan['requests_by_domain']['moneyflow_daily'][0]
            second = copy.deepcopy(first)
            first['params']['end_date'] = '20140115'
            second['params']['start_date'] = '20140201'
            plan['requests_by_domain']['moneyflow_daily'].append(second)
            return plan
        with patch.object(bootstrap_sources, 'plan_bootstrap_sources', new=gap):
            result = gate_a.validate_gate_a(self.plan)
        self.assertTrue(any(f['section'] == 'source:moneyflow_daily:tushare_pr7.v1'
                            and 'planner misses' in f['reason'] for f in result['findings']))

    def test_missing_policy_and_bypassed_shared_cap_block(self):
        real = source_completeness.completeness_policy
        def missing(profile, endpoint):
            policy = real(profile, endpoint)
            if endpoint == 'fina_indicator':
                policy = dict(policy, status='unestablished', completeness_rule=None)
            return policy
        with patch.object(source_completeness, 'completeness_policy', side_effect=missing):
            result = gate_a.validate_gate_a(self.plan)
        self.assertTrue(any(f['section'] == 'source:financial_events:tushare_fina_indicator.v1'
                            and 'policy unestablished' in f['reason'] for f in result['findings']))
        with patch.object(source_completeness, 'validate_payload_completeness', return_value={'admission': 'complete'}):
            result = gate_a.validate_gate_a(self.plan)
        self.assertTrue(any('unproven at-cap' in f['reason'] for f in result['findings']))

    def test_registry_public_binding_and_terminal_requirements_cannot_be_weakened(self):
        contract = gate_a._contract()
        contract['requirements']['market.open']['sources'] = ['benchmark_daily:tushare_dm1.v1']
        with patch.object(gate_a, '_contract', return_value=contract):
            result = gate_a.validate_gate_a(self.plan)
        self.assertEqual(result['status'], 'GATE_A_BLOCKED')
        self.assertTrue(any('source does not back' in f['reason'] for f in result['findings']))
        terminal = copy.deepcopy(self.plan['terminal_evidence_plan'])
        terminal['requirements'].pop('full_admission')
        with self.assertRaises(ArtifactError):
            gate_a.validate_terminal_evidence_plan(terminal)
        plan = copy.deepcopy(self.plan)
        plan['scope']['end_session'] = '2014-01-01'
        self.assertEqual(gate_a.validate_gate_a(plan)['status'], 'GATE_A_BLOCKED')

    def test_terminal_flags_cannot_substitute_for_bound_evidence(self):
        with self.assertRaises(ArtifactError):
            gate_a.validate_terminal_evidence('/unused', {'status': 'PASS'}, plan=self.plan['terminal_evidence_plan'])
        evidence = {name: {field: 'PASS' for field in spec['required']}
                    for name, spec in self.plan['terminal_evidence_plan']['requirements'].items()}
        result = gate_a.validate_terminal_evidence('/unused', evidence, plan=self.plan['terminal_evidence_plan'])
        self.assertEqual(result['status'], 'EVIDENCE_BLOCKED')
        self.assertFalse(result['ready_for_consumption'])

    def test_evidence_entry_signature_is_checked(self):
        real = gate_a._entry
        def entry(name):
            if name == 'axiom_data.operations.bootstrap':
                return lambda: None
            return real(name)
        with patch.object(gate_a, '_entry', side_effect=entry):
            result = gate_a.validate_terminal_evidence_plan(self.plan['terminal_evidence_plan'])
        self.assertTrue(any(m['category'] == 'baseline' and m['role'] == 'producer'
                            for m in result['missing_capabilities']))

    def test_historical_anchors_follow_each_identity_without_shrinking_target(self):
        target = dict(symbols=['600036.SH', '000001.SZ'], start_session='2025-06-10', end_session='2025-06-13')
        security = [dict(symbol='600036.SH', exchange='SSE', list_session='2000-01-01',
                         delist_session='2025-06-12', status='source-current-D'),
                    dict(symbol='000001.SZ', exchange='SZSE', list_session='2025-06-12',
                         delist_session=None, status='source-current-L')]
        security.sort(key=lambda row: row['symbol'])
        calendar = []
        for exchange in ('SSE', 'SZSE'):
            previous = None
            for day in ('2025-06-10', '2025-06-11', '2025-06-12', '2025-06-13'):
                calendar.append(dict(exchange=exchange, session=day, is_open=True, previous_open_session=previous))
                previous = day
        calendar.sort(key=lambda row: (row['session'], row['exchange']))
        result = gate_a.plan_historical_views(target=target, security_rows=security, calendar_rows=calendar,
                                             universe_ids=['000906.SH'], knowledge_cutoff='2025-06-13T23:59:59+08:00')
        self.assertEqual(result['status'], 'GEOMETRY_DEFINED')
        self.assertEqual(result['target'], target)
        self.assertEqual(result['views']['adjusted_price-600036.SH']['config']['anchor_session'], '2025-06-11')
        self.assertEqual(result['views']['adjusted_price-000001.SZ']['config']['anchor_session'], '2025-06-13')
        self.assertEqual(len(result['views']), 10)
        self.assertEqual({g['reason'] for g in result['identity_exclusions']}, {'delisted', 'not_yet_listed'})
        self.assertEqual(result['full_admission'], 'PENDING')
        next(row for row in security if row['symbol'] == '600036.SH')['list_session'] = None
        blocked = gate_a.plan_historical_views(target=target, security_rows=security, calendar_rows=calendar,
                                              universe_ids=['000906.SH'], knowledge_cutoff='2025-06-13T23:59:59+08:00')
        self.assertEqual(blocked['status'], 'GEOMETRY_BLOCKED')
        self.assertEqual(blocked['target'], target)
