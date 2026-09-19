import json
import shutil
import tempfile
import unittest
from pathlib import Path
from fixture_locations import fixture_root
from unittest.mock import patch

from axiom_data import (ArtifactError, BuildApplication, BuildContractError, BuildRequest,
                        MarketDomainBuilder, SnapshotReader, load_raw_batch,
                        validate_domain_commit_closure, write_raw_batch)
from axiom_data import operations, source_completeness
from axiom_data.artifacts import _digest
from axiom_data.contracts import writable_contracts
from axiom_data.historical_sparse import plan_historical_sparse
from axiom_data.pr7_source import Pr7Builder, Pr7Collector


class EmptySource:
    def query(self, endpoint, **params):
        if endpoint != 'forecast':
            raise AssertionError('unexpected fixture endpoint')
        return []


class WritableContractsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'data'
        self.run = json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        shutil.copytree(fixture_root(self.run['source_root']), self.root)
        self.addCleanup(self.cleanup)
        self.reader = SnapshotReader(self.root, self.run['refs']['snapshot_id'])
        self.security = self.reader.commits['security_master'].ref.commit_id
        self.spec = plan_historical_sparse(symbols=['688981.SH'], start_session='2025-06-10',
            end_session='2025-06-11')['requests_by_domain']['forecast_observations'][0]
        self.raw = Pr7Collector(self.root, EmptySource()).collect('forecast', self.spec['params'],
            retrieved_at='2026-09-13T00:00:00Z')

    def cleanup(self):
        for path in self.root.rglob('*'):
            if path.is_dir():
                path.chmod(0o755)
        self.temp.cleanup()

    def builder(self, config=None):
        return Pr7Builder(self.root, 'forecast_observations',
            builder_config=dict(forecast_source_types='forecast_source_types.v1', **(config or {})),
            dependency_commit_ids={'security_master': self.security})

    def inputs(self, version):
        return {'forecast_observations': dict(raw_batch_ids=[self.raw.raw_batch_id],
            contract_version=version, config={'forecast_source_types': 'forecast_source_types.v1'},
            new_lineage=True)}

    def test_legacy_published_snapshot_domain_and_raw_remain_readable(self):
        old = self.reader.commits['forecast_observations']
        self.assertEqual(old.ref.contract_version, 'forecast_observations.v1')
        checked = validate_domain_commit_closure(self.root, old.ref.domain, old.ref.commit_id)
        self.assertEqual(list(checked.rows), list(old.rows))
        for ref in checked.manifest['ordered_raw_batch_refs']:
            self.assertEqual(load_raw_batch(self.root, ref['raw_batch_id']).ref.manifest_digest,
                             ref['manifest_digest'])

    def test_superseded_domain_and_coverage_are_read_only_at_both_build_boundaries(self):
        for version, config in [('forecast_observations.v1', {}),
                                ('forecast_observations.v2', {'coverage_state_policy': 'source_observations.v1'}),
                                ('forecast_observations.v2', {'coverage_state_policy': None})]:
            with self.subTest(version=version, config=config):
                builder = self.builder(config)
                request = BuildRequest(None, [self.raw.raw_batch_id], [], version)
                with self.assertRaisesRegex((ArtifactError, BuildContractError), 'LEGACY_CONTRACT_READ_ONLY'):
                    BuildApplication('forecast_observations', builder).build(
                        None, [self.raw.raw_batch_id], [], version)
                with self.assertRaisesRegex(ArtifactError, 'LEGACY_CONTRACT_READ_ONLY'):
                    builder(request)

    def test_bootstrap_daily_repair_reject_legacy_before_publication(self):
        inputs = self.inputs('forecast_observations.v1')
        calls = [lambda: operations.bootstrap(self.root, run_id='legacy-bootstrap', domain_inputs=inputs),
                 lambda: operations.daily(self.root, run_id='legacy-daily',
                     snapshot_id=self.run['refs']['snapshot_id'], source_requests=[self.spec],
                     domain_inputs=inputs, client=EmptySource()),
                 lambda: operations.repair(self.root, run_id='legacy-repair',
                     snapshot_id=self.run['refs']['snapshot_id'], domain_inputs=inputs)]
        for call in calls:
            with self.assertRaisesRegex(ArtifactError, 'LEGACY_CONTRACT_READ_ONLY'):
                call()

    def test_missing_current_policy_cannot_be_bypassed_by_legacy_contract_or_coverage(self):
        extension = source_completeness._extension()
        extension['endpoints'].pop('forecast')
        with patch.object(source_completeness, '_extension', return_value=extension):
            for version, config in [('forecast_observations.v1', {}),
                                    ('forecast_observations.v2', {'coverage_state_policy': 'source_observations.v1'}),
                                    ('forecast_observations.v2', {})]:
                with self.subTest(version=version, config=config), self.assertRaises((ArtifactError, BuildContractError)):
                    BuildApplication('forecast_observations', self.builder(config)).build(
                        None, [self.raw.raw_batch_id], [], version)
            with self.assertRaisesRegex(ArtifactError, 'LEGACY_CONTRACT_READ_ONLY'):
                operations.bootstrap(self.root, run_id='missing-policy-legacy',
                                     domain_inputs=self.inputs('forecast_observations.v1'))

    def test_current_write_succeeds_and_upgrade_requires_new_lineage(self):
        builder = self.builder()
        self.assertEqual(builder.builder_config['coverage_state_policy'], 'source_observations.v2')
        old = self.reader.commits['forecast_observations']
        app = BuildApplication('forecast_observations', builder)
        with self.assertRaisesRegex(ArtifactError, 'different contract lineage'):
            app.build(old.ref.commit_id, [self.raw.raw_batch_id], [], 'forecast_observations.v2')
        ref = app.build(None, [self.raw.raw_batch_id], [], 'forecast_observations.v2')
        commit = validate_domain_commit_closure(self.root, ref.domain, ref.commit_id)
        self.assertIsNone(commit.manifest['parent_commit_ref'])
        self.assertEqual(commit.manifest['source_coverage']['schema_version'], 'source_observations.v2')
        self.assertTrue(commit.manifest['source_coverage']['observations'][0]['payload_admission']['complete'])
        self.assertEqual(len(commit.rows), 0)

    def test_base_builder_cannot_publish_unknown_source_by_omitting_coverage(self):
        raw = write_raw_batch(self.root, 'unestablished-source', domain='holder_count_events',
            source_profile='unestablished-source.v1', source_profile_version='unestablished-source.v1',
            source_profile_digest=_digest(b'unestablished-source'), request={'endpoint': 'unknown', 'params': {}},
            retrieved_at='2026-09-13T00:00:00Z', payload=b'[]', collector_code='fixture.v1')
        builder = MarketDomainBuilder(self.root, 'holder_count_events',
            dependency_commit_ids={'security_master': self.security})
        with self.assertRaisesRegex(ArtifactError, 'complete source evidence required'):
            BuildApplication('holder_count_events', builder).build(None, [raw.raw_batch_id], [], 'holder_count_events.v1')

    def test_registry_keeps_current_v1_distinct_from_superseded_versions(self):
        policy = writable_contracts()
        self.assertEqual(policy['domains']['market_daily']['current'], 'market_daily.v1')
        self.assertIn('forecast_observations.v1', policy['domains']['forecast_observations']['legacy_read_only'])
        self.assertEqual(policy['source_coverage']['legacy_read_only'], ['source_observations.v1'])

    def test_coverage_upgrade_requires_new_lineage_even_when_domain_contract_is_current(self):
        parent = self.reader.commits['holder_count_events']
        self.assertEqual(parent.ref.contract_version, writable_contracts()['domains']['holder_count_events']['current'])
        self.assertNotEqual(parent.manifest.get('source_coverage', {}).get('schema_version'), 'source_observations.v2')
        raw_ids = [ref['raw_batch_id'] for ref in parent.manifest['ordered_raw_batch_refs']]
        builder = Pr7Builder(self.root, 'holder_count_events',
            dependency_commit_ids={'security_master': self.security})
        app = BuildApplication('holder_count_events', builder)
        with self.assertRaisesRegex(ArtifactError, 'source coverage upgrade requires a new lineage'):
            app.build(parent.ref.commit_id, raw_ids, [], parent.ref.contract_version)
        result = app.build(None, raw_ids, [], parent.ref.contract_version)
        current = validate_domain_commit_closure(self.root, 'holder_count_events', result.commit_id)
        self.assertIsNone(current.manifest['parent_commit_ref'])
