import copy
import json
import unittest
from unittest.mock import patch

from axiom_data import ArtifactError, gate_a, operations, historical_sparse, source_completeness

_REAL_DAILY = operations.daily


def _daily_bypassing_writable_contract(data_root, *, run_id, snapshot_id, source_requests,
                                      domain_inputs, client=None, observed_raw_batch_ids=None):
    # Preserve request validation and normal writes; bypass only contract admission.
    if domain_inputs.get('financial_events', {}).get('contract_version') == 'financial_events.v1':
        return {'status': 'CANDIDATE_BUILT', 'ready_for_consumption': True}
    return _REAL_DAILY(data_root, run_id=run_id, snapshot_id=snapshot_id,
        source_requests=source_requests, domain_inputs=domain_inputs, client=client,
        observed_raw_batch_ids=observed_raw_batch_ids)


def _daily_without_admission(data_root, *, run_id, snapshot_id, source_requests,
                             domain_inputs, client=None, observed_raw_batch_ids=None):
    # Retaining all expected call names must not substitute for executing admission.
    if False:
        from axiom_data.operations import collect_requests, _validate_domain_inputs, assemble_candidate
        collect_requests()
        _validate_domain_inputs()
        assemble_candidate()
    return {'status': 'COMPLETE', 'ready_for_consumption': True}


class GateAFinalizationTest(unittest.TestCase):
    def setUp(self):
        self.plan = gate_a.make_gate_a_plan(dict(
            symbols=['688981.SH'], start_session='2014-01-01', end_session='2026-09-08',
            financial_observation_start='2013-01-01', benchmarks=['000300.SH'], universe_ids=['000906.SH']))

    def test_published_plan_and_report_round_trip_preserves_readiness(self):
        from axiom_data.artifacts import _json_bytes
        report = gate_a.validate_gate_a(self.plan)
        published_plan = json.loads(_json_bytes(self.plan))
        published_report = json.loads(_json_bytes(report))
        self.assertEqual(gate_a.validate_gate_a_report(published_report, plan=published_plan),
                         'GATE_A_READY_FOR_BULK_BUILD')

    def test_readiness_does_not_claim_gate_b_or_require_future_artifacts(self):
        result = gate_a.validate_gate_a(self.plan)
        self.assertEqual(result['status'], 'GATE_A_READY_FOR_BULK_BUILD', result['findings'])
        self.assertEqual(result['evidence']['terminal_plan']['schema_status'], 'PLAN_DEFINED')
        self.assertEqual(result['evidence']['terminal_plan']['execution_status'], 'CAPABILITY_READY')
        self.assertFalse(result['bulk_authorized'])
        self.assertFalse(result['ready_for_consumption'])
        self.assertEqual(result['gate_b_status'], 'NOT_ASSESSED')
        matrix = gate_a.completeness_matrix()
        self.assertEqual(len(matrix), 13)
        self.assertTrue(all(row['status'] == 'PASS' for row in matrix))
        self.assertEqual(result['evidence']['public_routes']['daily_contract_admission']['outcomes'],
                         {'financial_events.v1': 'LEGACY_CONTRACT_READ_ONLY',
                          'financial_events.v2': 'LEGACY_CONTRACT_READ_ONLY',
                          'financial_events.v3': 'LEGACY_CONTRACT_READ_ONLY',
                          'financial_events.v4': 'CANDIDATE_BUILT'})

    def test_new_registered_read_only_version_is_probed_automatically(self):
        from pathlib import Path
        from shutil import copytree
        from tempfile import TemporaryDirectory
        from axiom_data import contracts

        version = 'financial_events.v5'
        contract = contracts.load_contract('financial_events.v4')
        contract['contract_version'] = version
        policy = contracts.writable_contracts()
        policy['domains']['financial_events']['legacy_read_only'].append(version)
        gate_policy = gate_a._contract()
        gate_policy['writable_contracts_digest'] = gate_a._digest(gate_a._json_bytes(policy))
        with TemporaryDirectory() as tmp:
            package = Path(tmp) / 'contracts'
            copytree(Path(contracts.__file__).parent, package)
            (package / (version + '.json')).write_text(json.dumps(contract))
            (package / 'writable_contracts.v1.json').write_text(json.dumps(policy))
            with patch.dict(contracts._CONTRACT_FILES, {version: version + '.json'}), \
                    patch.dict(contracts._CONTRACT_DOMAINS, {version: 'financial_events'}), \
                    patch.object(contracts, 'files', return_value=package), \
                    patch.object(gate_a, '_contract', return_value=gate_policy):
                result = gate_a.validate_gate_a(self.plan)
        self.assertEqual(result['status'], 'GATE_A_READY_FOR_BULK_BUILD', result['findings'])
        outcomes = result['evidence']['public_routes']['daily_contract_admission']['outcomes']
        self.assertEqual(len(outcomes), 5)
        self.assertEqual(outcomes[version], 'LEGACY_CONTRACT_READ_ONLY')

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

    def test_daily_false_readiness_blocks_even_with_all_route_names(self):
        import axiom_data
        with patch.object(operations, 'daily', _daily_without_admission), \
                patch.object(axiom_data, 'daily', _daily_without_admission):
            result = gate_a.validate_gate_a(self.plan)
        self.assertEqual(result['status'], 'GATE_A_BLOCKED', result['findings'])
        self.assertTrue(any(f['section'] == 'public_routes' and 'daily' in f['reason']
                            for f in result['findings']))

    def test_legal_daily_request_cannot_bypass_writable_contract_admission(self):
        import axiom_data
        # Keep source inventory unchanged: only executed behavior can catch this fault.
        from functools import wraps
        bypass = wraps(_REAL_DAILY)(_daily_bypassing_writable_contract)
        with patch.object(operations, 'daily', bypass), patch.object(axiom_data, 'daily', bypass):
            result = gate_a.validate_gate_a(self.plan)
        self.assertEqual(result['status'], 'GATE_A_BLOCKED', result['findings'])
        self.assertTrue(any(f['section'] == 'public_routes' and 'daily' in f['reason']
                            and 'contract' in f['reason'] for f in result['findings']))

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
