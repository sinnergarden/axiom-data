"""Publication-date boundaries and unresolved financial content in v4."""
import tempfile
import unittest
from pathlib import Path

from axiom_data import BuildApplication, MarketDomainBuilder, load_raw_batch, validate_domain_commit_closure
from axiom_data.artifacts import ArtifactError
from axiom_data.contracts import load_contract
from axiom_data.pit import select_revisions
from axiom_data.pr6_source import Pr6Builder, Pr6Collector, load_pr6_source_profile
from test_artifacts import security_row, write_rows, synthetic_source_fixture

ENDPOINTS = ('income', 'balancesheet', 'cashflow', 'fina_indicator')


@synthetic_source_fixture
class FinancialV4Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        ref = write_rows(self.root, 'security', 'security_master',
                         [security_row('000001.SZ'), security_row('000002.SZ')])
        self.security = BuildApplication('security_master', MarketDomainBuilder(self.root, 'security_master')).build(
            None, [ref.raw_batch_id], [], 'security_master.v1')

    def record(self, endpoint, value, flag='1', **fields):
        profile = 'tushare_fina_indicator.v1' if endpoint == 'fina_indicator' else 'tushare_pr6.v1'
        definition = load_pr6_source_profile(profile)['endpoints'][endpoint]
        row = dict(ts_code='000001.SZ', end_date='20241231', ann_date='20250401', update_flag=flag)
        if endpoint != 'fina_indicator':
            row.update(f_ann_date='20250401', report_type='1')
        row.update({field: value for field in definition['mapping']})
        row.update(fields)
        return row

    def collect(self, endpoint, records, when='2025-05-01T00:00:00Z'):
        class Client:
            def query(self, *args, **kwargs): return records
        profile = 'tushare_fina_indicator.v1' if endpoint == 'fina_indicator' else 'tushare_pr6.v1'
        ref = Pr6Collector(self.root, Client()).collect(endpoint,
            {'ts_code': records[0]['ts_code'], 'period': '20241231'},
            retrieved_at=when, profile_version=profile)
        return load_raw_batch(self.root, ref.raw_batch_id)

    def builder(self):
        return Pr6Builder(self.root, 'financial_events', builder_config={'symbols': ['000001.SZ', '000002.SZ']},
                          dependency_commit_ids={'security_master': self.security.commit_id})

    def mapped(self, raws):
        return self.builder()._build_rows(load_contract('financial_events.v4'), (), raws)

    def publish(self, raws):
        ref = BuildApplication('financial_events', self.builder()).build(
            None, [r.ref.raw_batch_id for r in raws], [], 'financial_events.v4')
        return validate_domain_commit_closure(self.root, 'financial_events', ref.commit_id)

    def test_all_endpoints_public_preference_and_raw_unchanged(self):
        for endpoint in ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                a, b = self.record(endpoint, 100, '0'), self.record(endpoint, 200)
                raw = self.collect(endpoint, [a, b]); original = raw.payload
                checked = self.publish([raw])
                expected = self.mapped([self.collect(endpoint, [b])])
                self.assertEqual(len(checked.rows), 1)
                self.assertEqual(checked.rows[0]['values'], expected[0]['values'])
                self.assertEqual(checked.rows[0]['revision_id'], expected[0]['revision_id'])
                self.assertEqual(checked.rows[0]['source_ref'], raw.ref.raw_batch_id)
                self.assertEqual(load_raw_batch(self.root, raw.ref.raw_batch_id).payload, original)
                self.assertEqual([r['revision_id'] for r in self.mapped([self.collect(endpoint, [b, a, b])])],
                                 [checked.rows[0]['revision_id']])

    def test_same_flag_conflicts_are_published_but_never_guessed(self):
        for endpoint in ENDPOINTS:
            for flag in ('0', '1'):
                with self.subTest(endpoint=endpoint, flag=flag):
                    raw = self.collect(endpoint, [self.record(endpoint, 100, flag), self.record(endpoint, 200, flag)])
                    checked = self.publish([raw])
                    self.assertEqual(len(checked.rows), 2)
                    for policy in ('best_effort_vendor_v1', 'operational_pit_v1'):
                        with self.assertRaisesRegex(ValueError, 'ambiguous simultaneous revisions'):
                            select_revisions(checked.rows, policy=policy, knowledge_cutoff='2025-06-01T00:00:00Z')

    def test_both_publication_dates_bound_preference(self):
        for endpoint in ENDPOINTS:
            for changed in (('ann_date',) if endpoint == 'fina_indicator' else ('ann_date', 'f_ann_date')):
                with self.subTest(endpoint=endpoint, changed=changed):
                    a = self.record(endpoint, 100, '0')
                    b = self.record(endpoint, 200, '1', **{changed: '20250402'})
                    rows = self.mapped([self.collect(endpoint, [a, b])])
                    self.assertEqual(len(rows), 2)
                    with self.assertRaisesRegex(ValueError, 'ambiguous simultaneous revisions'):
                        select_revisions(rows, policy='operational_pit_v1', knowledge_cutoff='2025-06-01T00:00:00Z')
                    if endpoint == 'fina_indicator' or changed == 'f_ann_date':
                        early = select_revisions(rows, policy='best_effort_vendor_v1', knowledge_cutoff='2025-04-01T16:00:00Z')
                        late = select_revisions(rows, policy='best_effort_vendor_v1', knowledge_cutoff='2025-04-03T00:00:00Z')
                        self.assertEqual(len(early), 1); self.assertEqual(len(late), 1)
                        self.assertNotEqual(early[0]['revision_id'], late[0]['revision_id'])

    def test_separate_raw_observation_does_not_inherit_flag_preference(self):
        a = self.collect('income', [self.record('income', 200, '1')])
        b = self.collect('income', [self.record('income', 100, '0')], when='2025-05-02T00:00:00Z')
        rows = self.mapped([b, a])
        old = select_revisions(rows, policy='operational_pit_v1', knowledge_cutoff='2025-05-01T12:00:00Z')
        new = select_revisions(rows, policy='operational_pit_v1', knowledge_cutoff='2025-05-03T00:00:00Z')
        self.assertEqual(old[0]['values']['revenue'], 200)
        self.assertEqual(new[0]['values']['revenue'], 100)

    def test_unrequested_security_conflict_is_not_projected_away(self):
        good = self.collect('income', [self.record('income', 100)])
        bad = self.collect('income', [self.record('income', v, ts_code='000002.SZ') for v in (100, 200)])
        checked = self.publish([good, bad])
        self.assertEqual({r['symbol'] for r in checked.rows}, {'000001.SZ', '000002.SZ'})
        with self.assertRaisesRegex(ValueError, 'ambiguous simultaneous revisions'):
            select_revisions(checked.rows, policy='best_effort_vendor_v1', knowledge_cutoff='2025-06-01T00:00:00Z')

    def test_unknown_flags_fail_and_identical_duplicates_still_deduplicate(self):
        row = self.record('income', 100)
        self.assertEqual(len(self.mapped([self.collect('income', [row, row])])), 1)
        for flag in ('2', None, 1):
            with self.assertRaisesRegex(ArtifactError, 'unknown financial update_flag'):
                self.mapped([self.collect('income', [dict(row, update_flag=flag)])])
