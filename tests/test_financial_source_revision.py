import json
import shutil
import tempfile
import unittest
from pathlib import Path

from axiom_data import BuildApplication, BuildContractError, MarketDomainBuilder, load_raw_batch, validate_domain_commit_closure
from axiom_data.artifacts import ArtifactError
from axiom_data.contracts import load_contract
from axiom_data.pit import select_revisions
from axiom_data.pr6_source import Pr6Builder, Pr6Collector
from test_artifacts import security_row, write_rows, synthetic_source_fixture

RAW_ID = 'pr6-480cfa7d0400011c3adfcb532a724f202e65fbe15663730d840bbe37bd6a5b9c'
FIXTURE = Path(__file__).parent / 'fixtures' / 'financial_revision' / RAW_ID


@synthetic_source_fixture
class FinancialSourceRevisionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(FIXTURE, self.root / 'raw' / 'batches' / RAW_ID)
        self.raw = load_raw_batch(self.root, RAW_ID)
        self.source = json.loads(self.raw.payload)
        ref = write_rows(self.root, 'security-fixture', 'security_master',
                        [security_row('002010.SZ')], retrieved_at='2025-01-01T00:00:00Z')
        self.security = BuildApplication('security_master', MarketDomainBuilder(self.root, 'security_master')).build(
            None, [ref.raw_batch_id], [], 'security_master.v1')

    def builder(self):
        return Pr6Builder(self.root, 'financial_events',
            dependency_commit_ids={'security_master': self.security.commit_id})

    def mapped(self, raw=None, version='financial_events.v3'):
        return self.builder()._build_rows(load_contract(version), [], [raw or self.raw])

    def collect(self, rows, when='2026-09-14T06:40:58.094391+00:00'):
        class Client:
            def query(self, *args, **kwargs):
                return rows
        ref = Pr6Collector(self.root, Client()).collect('fina_indicator', self.raw.manifest['request']['params'],
            retrieved_at=when, profile_version='tushare_fina_indicator.v1')
        return load_raw_batch(self.root, ref.raw_batch_id)

    def test_actual_frozen_raw_public_build_and_replay(self):
        self.assertEqual([r['update_flag'] for r in self.source], ['1', '0'])
        ref = BuildApplication('financial_events', self.builder()).build(None, [RAW_ID], [], 'financial_events.v4')
        checked = validate_domain_commit_closure(self.root, 'financial_events', ref.commit_id)
        self.assertEqual(len(checked.rows), 1)
        self.assertEqual(checked.rows[0]['values']['roe'], 0.024916)
        self.assertEqual(checked.rows[0]['source_ref'], RAW_ID)
        self.assertEqual(load_raw_batch(self.root, RAW_ID).payload, self.raw.payload)
        self.assertEqual(checked.rows[0]['first_observed_at'], self.raw.manifest['retrieved_at'])
        for policy in ('operational_pit_v1', 'best_effort_vendor_v1'):
            winner = select_revisions(checked.rows, policy=policy, knowledge_cutoff='2026-09-15T00:00:00Z')
            self.assertEqual(winner[0]['values']['roe'], 0.024916)

    def test_row_order_and_identical_duplicate(self):
        expected = self.mapped()
        for rows in (list(reversed(self.source)), self.source + self.source):
            result = self.mapped(self.collect(rows))
            self.assertEqual([r['revision_id'] for r in result], [r['revision_id'] for r in expected])
        duplicate = self.collect([self.source[0], self.source[0]])
        self.assertEqual(self.mapped(duplicate), self.mapped(duplicate, 'financial_events.v2'))

    def test_conflicting_equal_flags_and_unknown_fail(self):
        for flag in ('0', '1', '2', None, 1):
            with self.subTest(flag=flag):
                raw = self.collect([dict(r, update_flag=flag) for r in self.source])
                with self.assertRaisesRegex(ArtifactError, 'ambiguous simultaneous revisions'):
                    self.mapped(raw)
        for flag in ('0', '1'):
            raw = self.collect(self.source + [dict(self.source[0], update_flag=flag, roe=99)])
            with self.assertRaisesRegex(ArtifactError, 'ambiguous simultaneous revisions'):
                self.mapped(raw)

    def test_old_contract_keeps_conflict(self):
        for version in ('financial_events.v1', 'financial_events.v2'):
            rows = self.mapped(version=version)
            self.assertEqual(len(rows), 2)
            with self.assertRaisesRegex(ValueError, 'ambiguous simultaneous revisions'):
                select_revisions(rows, policy='operational_pit_v1', knowledge_cutoff='2026-09-15T00:00:00Z')

    def test_publication_cannot_downgrade_contract(self):
        for version in ('financial_events.v1', 'financial_events.v2', 'financial_events.v3'):
            with self.assertRaisesRegex((ArtifactError, BuildContractError), 'LEGACY_CONTRACT_READ_ONLY'):
                BuildApplication('financial_events', self.builder()).build(None, [RAW_ID], [], version)

    def test_different_observation_or_announcement_is_not_overridden(self):
        later = self.collect([self.source[1]], '2026-09-16T00:00:00Z')
        rows = self.builder()._build_rows(load_contract('financial_events.v3'), [], [self.raw, later])
        selected = select_revisions(rows, policy='operational_pit_v1', knowledge_cutoff='2026-09-17T00:00:00Z')
        self.assertEqual(selected[0]['values']['roe'], 0.024915)
        raw = self.collect([self.source[0], dict(self.source[1], ann_date='20260829')])
        self.assertEqual(len(self.mapped(raw)), 2)


class HistoricalFinancialArtifactTest(unittest.TestCase):
    """Explicit immutable external fixture; no Snapshot initialization."""
    def test_published_old_contract_closure_and_upgrade_boundary(self):
        from fixture_locations import fixture_root
        from axiom_data.contracts import require_writable_contract
        evidence = json.loads((Path(__file__).parents[1] / 'reports/pr6/run_manifest.json').read_bytes())
        root = fixture_root(evidence['data_root'])
        expected = evidence['artifact_refs']['domain_commits']['financial_events']
        old = validate_domain_commit_closure(root, 'financial_events', expected['domain_commit_id'])
        self.assertEqual(old.ref.contract_version, 'financial_events.v1')
        self.assertEqual(len(old.rows), expected['rows'])
        self.assertEqual(old.manifest['logical_content_digest'], expected['logical_content_digest'])
        for version in ('financial_events.v1', 'financial_events.v2'):
            with self.assertRaisesRegex(ValueError, 'LEGACY_CONTRACT_READ_ONLY'):
                require_writable_contract('financial_events', version)


if __name__ == '__main__':
    unittest.main()
