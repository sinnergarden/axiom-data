import copy
import unittest
from unittest.mock import patch

from axiom_data import ArtifactError, gate_a, operations, historical_sparse, source_completeness


class GateAFinalizationTest(unittest.TestCase):
    def setUp(self):
        self.plan = gate_a.make_gate_a_plan(dict(
            symbols=['688981.SH'], start_session='2014-01-01', end_session='2026-09-08',
            financial_observation_start='2013-01-01', benchmarks=['000300.SH'], universe_ids=['000906.SH']))

    def test_readiness_does_not_claim_gate_b_or_require_future_artifacts(self):
        result = gate_a.validate_gate_a(self.plan)
        self.assertEqual(result['status'], 'GATE_A_READY_FOR_BULK_BUILD', result['findings'])
        self.assertEqual(result['evidence']['terminal_plan']['schema_status'], 'PLAN_DEFINED')
        self.assertEqual(result['evidence']['terminal_plan']['execution_status'], 'CAPABILITY_BLOCKED')
        self.assertFalse(result['bulk_authorized'])
        self.assertFalse(result['ready_for_consumption'])
        self.assertEqual(result['gate_b_status'], 'NOT_ASSESSED')
        matrix = gate_a.completeness_matrix()
        self.assertEqual(len(matrix), 13)
        self.assertTrue(all(row['status'] == 'PASS' for row in matrix))

    def test_missing_policy_bad_pagination_and_unknown_history_block_real_gate(self):
        original = source_completeness._extension()
        changes = []
        missing = copy.deepcopy(original); missing['endpoints'].pop('daily'); changes.append(missing)
        terminal = copy.deepcopy(original)
        terminal['endpoints']['index_member_all'].pop('terminal_page_rule'); changes.append(terminal)
        unknown = copy.deepcopy(original); unknown['historical_completeness'] = 'unknown'; changes.append(unknown)
        for extension in changes:
            with self.subTest(extension=extension.get('historical_completeness')):
                with patch.object(source_completeness, '_extension', return_value=extension):
                    result = gate_a.validate_gate_a(self.plan)
                self.assertEqual(result['status'], 'GATE_A_BLOCKED')
                self.assertTrue(any(f['section'].startswith('source:') for f in result['findings']))

    def test_sparse_endpoint_removal_blocks(self):
        with patch.object(historical_sparse, 'ENDPOINTS', tuple(e for e in historical_sparse.ENDPOINTS if e != 'forecast')):
            result = gate_a.validate_gate_a(self.plan)
        self.assertEqual(result['status'], 'GATE_A_BLOCKED')
        self.assertTrue(any(f['section'] == 'historical_sparse' for f in result['findings']))

    def test_missing_actual_bulk_entry_is_not_a_deferred_gate_b_gap(self):
        for entry in ('axiom_data.view_operation.materialize_views',
                      'axiom_data.bootstrap_sources.collect_bootstrap_sources'):
            with self.subTest(entry=entry):
                with patch(entry, None):
                    result = gate_a.validate_gate_a(self.plan)
                self.assertEqual(result['status'], 'GATE_A_BLOCKED')
                self.assertTrue(any(f['section'] in {'public_routes', 'execution_entries'} for f in result['findings']))

    def test_real_public_route_bypass_and_dead_code_block(self):
        def bypass(*args, **kwargs):
            if False:
                operations.assemble_candidate(*args, **kwargs)
            return {'status': 'CANDIDATE_BUILT'}
        for name in ('bootstrap', 'repair'):
            with self.subTest(name=name):
                with patch.object(operations, name, bypass):
                    result = gate_a.validate_gate_a(self.plan)
                self.assertEqual(result['status'], 'GATE_A_BLOCKED')
                self.assertTrue(any(f['section'] == 'public_routes' for f in result['findings']))

    def test_pass_cannot_be_reused_after_code_or_contract_change(self):
        report = gate_a.validate_gate_a(self.plan)
        self.assertEqual(gate_a.validate_gate_a_report(report, plan=self.plan), report['status'])
        altered = copy.deepcopy(report); altered['bulk_authorized'] = True
        with self.assertRaises(ArtifactError):
            gate_a.validate_gate_a_report(altered, plan=self.plan)
        identity = gate_a.code_identity(); identity['implementation_digest'] = 'sha256:' + '0' * 64
        with patch.object(gate_a, 'code_identity', return_value=identity), self.assertRaises(ArtifactError):
            gate_a.validate_gate_a_report(report, plan=self.plan)

    def test_accepted_reference_evidence_cannot_be_replaced(self):
        contract = gate_a._contract()
        contract['reference_qualification']['industry']['content_digest'] = 'sha256:' + '0' * 64
        with patch.object(gate_a, '_contract', return_value=contract):
            result = gate_a.validate_gate_a(self.plan)
        self.assertEqual(result['status'], 'GATE_A_BLOCKED')
        self.assertTrue(any(f['section'] == 'reference_qualification' for f in result['findings']))
