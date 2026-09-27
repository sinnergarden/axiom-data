import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from axiom_data import ArtifactError
from axiom_data.publication import readonly_publication


spec = importlib.util.spec_from_file_location('bootstrap_references_script',
    Path(__file__).resolve().parents[1] / 'scripts' / 'bootstrap_references.py')
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


class BootstrapReferencesScriptTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'operations' / 'parent-run' / 'source-plans' / 'market.json'
        self.source.parent.mkdir(parents=True)
        self.scope = {'symbols': ['000001.SZ'], 'start_session': '2014-01-01',
                      'end_session': '2026-09-11', 'financial_observation_start': '2012-01-01',
                      'benchmarks': ['000300.SH'], 'universe_ids': ['000300.SH']}
        self.source.write_text(json.dumps({'scope': self.scope}))
        self.config = {'official_termination_raw_batch_ids': {'SZSE': 'official-sz'}}
        self.domains = ['trading_calendar', 'security_master', 'industry_membership']
        self.collection = {'status': 'COMPLETE', 'raw_batch_ids': {
            domain: [domain + '-raw'] for domain in self.domains},
            'request_count': 9, 'raw_batch_count': 9, 'rows': 100}
        official = SimpleNamespace(manifest={
            'domain': 'security_master', 'source_profile_version': 'exchange_security.v1',
            'request': script.profile()['endpoints']['SZSE']})
        self.enterContext(patch.object(script, 'load_raw_batch', return_value=official))
        self.enterContext(patch.object(script, 'validate_raw_completeness', return_value={'complete': True}))
        self.collect = self.enterContext(patch.object(script, 'collect_reference_sources', return_value=self.collection))
        self.builders = {name: self.enterContext(patch.object(script, name))
                         for name in ['TushareMarketBuilder', 'ExchangeSecurityBuilder', 'FundamentalsBuilder']}
        self.calls = []

        def application(domain, builder):
            def build(parent, raw, patches, contract):
                self.assertTrue(readonly_publication.get())
                self.calls.append((domain, parent, raw, patches, contract))
                return SimpleNamespace(commit_id=domain + '-commit', contract_version=contract)
            return SimpleNamespace(build=build)

        self.enterContext(patch.object(script, 'BuildApplication', side_effect=application))
        def checked(root, domain, identity):
            return SimpleNamespace(ref=SimpleNamespace(commit_id=identity,
                contract_version=domain + ('.v3' if domain == 'industry_membership' else '.v2' if domain == 'security_master' else '.v1')))
        self.validate = self.enterContext(patch.object(script, 'validate_domain_commit_closure', side_effect=checked))

    def run_script(self, **changes):
        args = dict(run_id='parent-run-references', parent_run_id='parent-run',
                    source_plan_path=self.source, config=self.config)
        return script.bootstrap_references(self.root, **dict(args, **changes))

    def test_frozen_scope_and_official_refs_reach_existing_public_builders(self):
        result = self.run_script()
        self.assertEqual(result['status'], 'REFERENCES_VALIDATED')
        self.assertEqual([call[0] for call in self.calls], self.domains)
        self.assertTrue(all(call[1] is None and call[3] == [] for call in self.calls))
        self.assertEqual(self.calls[1][2], ['security_master-raw', 'official-sz'])
        self.assertEqual(self.validate.call_count, 3)
        context = self.collect.call_args.kwargs['context']
        self.assertEqual(context['config'], self.config)
        self.assertEqual(context['parent_run_id'], 'parent-run')
        self.assertEqual(context['builds']['industry_membership']['config']['end_session'], '2026-09-11')
        self.assertEqual(self.builders['FundamentalsBuilder'].call_args.kwargs['dependency_commit_ids'],
                         {'security_master': 'security_master-commit'})
        self.assertFalse(result['ready_for_consumption'])
        saved = self.root / 'operations' / 'parent-run-references' / 'reference-report.json'
        self.assertEqual(json.loads(saved.read_bytes()), result)

    def test_failed_build_replaces_previous_success_with_durable_failure(self):
        self.run_script()
        self.validate.side_effect = ArtifactError('supplier details must not be persisted')
        with self.assertRaises(ArtifactError):
            self.run_script()
        saved = self.root / 'operations' / 'parent-run-references' / 'reference-report.json'
        result = json.loads(saved.read_bytes())
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(result['error_type'], 'ArtifactError')
        self.assertEqual(result['domain_commit_ids'], {})
        self.assertNotIn('supplier details', saved.read_text())

    def test_partial_collection_does_not_start_builds(self):
        self.run_script()
        self.calls.clear()
        self.collect.return_value = dict(self.collection, status='PARTIAL')
        result = self.run_script()
        self.assertEqual(result['stage'], 'SOURCE_COLLECTION')
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(result['domain_commit_ids'], {})
        self.assertFalse(self.calls)
        saved = self.root / 'operations' / 'parent-run-references' / 'reference-report.json'
        self.assertEqual(json.loads(saved.read_bytes()), result)

    def test_collector_cannot_change_build_refs_by_mutating_caller_config(self):
        def collect(*args, **kwargs):
            self.config['official_termination_raw_batch_ids']['SZSE'] = 'different-official-raw'
            self.config['page_size'] = 500
            return self.collection
        self.collect.side_effect = collect
        self.run_script()
        self.assertEqual(self.calls[1][2], ['security_master-raw', 'official-sz'])
        self.assertEqual(self.collect.call_args.kwargs['context']['config'],
                         {'official_termination_raw_batch_ids': {'SZSE': 'official-sz'}})

    def test_foreign_plan_and_missing_official_refs_fail_before_collection(self):
        for changes in ({'parent_run_id': 'other-parent'},
                        {'config': {'official_termination_raw_batch_ids': {}}},
                        {'source_plan_path': str(self.source.parent / '..' / 'market.json')}):
            with self.subTest(changes=changes), self.assertRaises(ArtifactError):
                self.run_script(**changes)
        self.collect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
