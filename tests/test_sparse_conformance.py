import copy
import unittest
from unittest.mock import patch

from axiom_data import ArtifactError, gate_a, historical_sparse, sparse_conformance


class SparseConformanceTest(unittest.TestCase):
    def _gate_plan(self):
        return gate_a.make_gate_a_plan(dict(symbols=['600036.SH'],
            start_session='2014-01-01', end_session='2014-01-04',
            financial_observation_start='2013-01-01',
            benchmarks=['000300.SH'], universe_ids=['000906.SH']))

    def _assert_gate_blocked(self):
        report = gate_a.validate_gate_a(self._gate_plan())
        self.assertEqual(report['status'], 'GATE_A_BLOCKED')
        self.assertTrue(any(f['section'] == 'historical_sparse' for f in report['findings']),
                        report['findings'])

    def test_public_fixed_fixtures_and_checkpoint_resume_are_stable(self):
        report = sparse_conformance.run_sparse_conformance()
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(report, sparse_conformance.run_sparse_conformance())
        cases = {case['fixture_id']: case for case in report['cases']}
        self.assertEqual(set(cases), set(sparse_conformance.CASES) | {'gap_guard_isolation'})
        self.assertTrue(all(case['passed'] for case in cases.values()))
        valid = cases['exact_complete_children']
        self.assertEqual(valid['child_count'], 2)
        self.assertEqual(valid['source_query_count'], 3)
        self.assertEqual(valid['resume_query_count'], 0)
        self.assertEqual(valid['admitted_empty_observations'], 2)
        self.assertEqual(cases['missing_child']['child_statuses'], ['NOT_QUERIED', 'COMPLETE'])
        self.assertEqual(cases['gap_guard_isolation']['boundary_injection'],
                         'controlled_split_provenance_isolation_not_public_source_proof')
        self.assertEqual(report['qualification'], 'request_complete_best_effort')
        self.assertEqual(report['empty_result_semantics'], 'no_source_events_in_scope')
        self.assertFalse(report['ready_for_consumption'])

    def test_always_complete_public_validator_blocks_gate_a(self):
        real = historical_sparse.validate_sparse_coverage

        def always_complete(*args, **kwargs):
            try:
                result = real(*args, **kwargs)
            except ArtifactError:
                result = {'coverage': [{'children': []}]}
            return dict(result, status='COMPLETE', complete=True)

        with patch.object(historical_sparse, 'validate_sparse_coverage', new=always_complete):
            report = sparse_conformance.run_sparse_conformance()
            self.assertEqual(report['status'], 'BLOCKED')
            missing = next(case for case in report['cases'] if case['fixture_id'] == 'missing_child')
            self.assertEqual(missing['actual'], 'COMPLETE')
            self.assertFalse(missing['passed'])
            self._assert_gate_blocked()

    def test_non_function_validator_is_exercised_instead_of_rejected_by_reflection(self):
        plan = gate_a.make_gate_a_plan(dict(symbols=['600036.SH'], start_session='2014-01-01',
            end_session='2014-01-04', financial_observation_start='2013-01-01',
            benchmarks=['000300.SH'], universe_ids=['000906.SH']))
        with patch.object(historical_sparse, 'validate_sparse_coverage', return_value={
                'status': 'COMPLETE', 'complete': True, 'coverage': []}) as fake:
            report = gate_a.validate_gate_a(plan)
        self.assertGreater(fake.call_count, 0)
        self.assertEqual(report['status'], 'GATE_A_BLOCKED')
        behavior = report['evidence']['historical_sparse']['behavioral_conformance']
        self.assertEqual(behavior['status'], 'BLOCKED')
        self.assertTrue(all(case['actual'] == 'SETUP_ERROR' for case in behavior['cases']))

    def test_removed_child_completeness_behavior_blocks_gate_a(self):
        real = historical_sparse.validate_sparse_coverage

        def ignores_incomplete_child(*args, **kwargs):
            result = copy.deepcopy(real(*args, **kwargs))
            # Model loss of all(child.complete): any surviving observation is
            # incorrectly promoted to complete parent coverage.
            parent = result['coverage'][0]
            if parent['observations']:
                parent.update(status='COMPLETE', complete=True)
                result.update(status='COMPLETE', complete=True)
            return result

        with patch.object(historical_sparse, 'validate_sparse_coverage', new=ignores_incomplete_child):
            report = sparse_conformance.run_sparse_conformance()
            self.assertEqual(report['status'], 'BLOCKED')
            missing = next(case for case in report['cases'] if case['fixture_id'] == 'missing_child')
            self.assertEqual(missing['actual'], 'COMPLETE')
            self.assertFalse(missing['passed'])
            self._assert_gate_blocked()

    def test_removed_gap_guard_is_not_hidden_by_split_provenance(self):
        real = historical_sparse._check_children

        def ignores_intervals(parent, children):
            try:
                real(parent, children)
            except ArtifactError as exc:
                if 'interval' not in str(exc):
                    raise

        with patch.object(historical_sparse, '_check_children', new=ignores_intervals):
            report = sparse_conformance.run_sparse_conformance()
            cases = {case['fixture_id']: case for case in report['cases']}
            self.assertEqual(report['status'], 'BLOCKED')
            self.assertTrue(cases['interval_gap']['passed'])
            self.assertEqual(cases['gap_guard_isolation']['actual'], 'COMPLETE')
            self.assertFalse(cases['gap_guard_isolation']['passed'])
            self._assert_gate_blocked()

    def test_fixture_setup_failure_is_never_a_successful_rejection(self):
        with patch.object(sparse_conformance, '_replace_raw', side_effect=ArtifactError(
                'possibly truncated source payload; split bounded request')):
            report = sparse_conformance.run_sparse_conformance()
        self.assertEqual(report['status'], 'BLOCKED')
        case = next(case for case in report['cases'] if case['fixture_id'] == 'truncated_child')
        self.assertEqual(case['actual'], 'SETUP_ERROR')
        self.assertFalse(case['passed'])
