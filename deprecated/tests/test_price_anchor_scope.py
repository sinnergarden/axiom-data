"""Regression boundaries for fixed scope and source-backed anchor planning."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from axiom_data import ArtifactError, FactView, SnapshotReader, materialize_views
from axiom_data.admission_plan import resolve_price_anchors
from axiom_data.gate_a import plan_historical_views
from fixture_locations import fixture_root


class HistoricalAnchorTest(unittest.TestCase):
    def reader(self, days=('2020-08-26',)):
        security = [dict(symbol='600069.SH', exchange='SSE', list_session='1997-04-30',
                         delist_session='2020-08-28', status='source-current-D')]
        calendar = [dict(exchange='SSE', session=d, is_open=True, previous_open_session=p)
                    for d, p in [('2020-08-26', None), ('2020-08-27', '2020-08-26'),
                                 ('2020-08-28', '2020-08-27')]]
        return SimpleNamespace(data_root='fixture', commits={'adjustment_factors': SimpleNamespace(
            ref=SimpleNamespace(commit_id='fixed-factor'), manifest={'ordered_raw_batch_refs': [{'raw_batch_id': 'fixed-source'}], 'parent_commit_ref': None})}, snapshot=SimpleNamespace(
            ref=SimpleNamespace(snapshot_id='fixture-snapshot', manifest_digest='fixed-digest'),
            manifest={'domain_refs': {'adjustment_factors': {'domain_commit_id': 'fixed-factor'}}}),
            security_master=lambda: security, trading_calendar=lambda **kw: calendar,
            session_rows=Mock(return_value=iter(dict(symbol='600069.SH', session=d,
                factor=2, source_ref='fixed-source', source_available_at=None,
                first_observed_at='2020-08-28T12:00:00+08:00',
                availability_basis='terminal_history_observed', pit_qualification='best_effort') for d in days)), _check_consumed_metadata=Mock())

    def plan(self, reader, raw_error=None):
        with patch('axiom_data.consumption.SnapshotReader', return_value=reader),\
                patch('axiom_data.artifacts.load_raw_batch', side_effect=raw_error, return_value=SimpleNamespace(manifest={'domain': 'adjustment_factors', 'retrieved_at': '2020-08-28T12:00:00+08:00'})),\
                patch('axiom_data.artifacts._raw_ref', return_value={'raw_batch_id': 'fixed-source'}):
            return plan_historical_views(target=dict(symbols=['600069.SH'], start_session='2020-08-26',
                end_session='2020-08-28'), security_rows=reader.security_master(),
                calendar_rows=reader.trading_calendar(), universe_ids=['000906.SH'],
                knowledge_cutoff='2020-08-28T23:59:59+08:00', data_root='fixture', snapshot_id='fixture-snapshot')

    def test_last_observed_anchor_does_not_erase_missing_day(self):
        reader = self.reader()
        plan = self.plan(reader)
        config = plan['views']['adjusted_price-600069.SH']['config']
        self.assertEqual(config['anchor_session'], '2020-08-26')
        self.assertEqual(config['end_session'], '2020-08-27')
        self.assertEqual(plan['expected_sessions']['600069.SH'], ['2020-08-26', '2020-08-27'])
        self.assertEqual(plan['target']['end_session'], '2020-08-28')
        self.assertEqual(plan['price_anchor_resolution']['anchors']['adjusted_price-600069.SH']
                         ['requested_anchor_upper_bound'], '2020-08-27')
        self.assertEqual(plan['source_availability_validation'], 'PENDING')
        self.assertFalse(plan['ready_for_consumption'])
        reader.session_rows.assert_called_once()

    def test_no_factor_or_only_after_upper_bound_fails(self):
        for days in ((), ('2020-08-28',), ('2020-08-25',)):
            with self.subTest(days=days), self.assertRaisesRegex(ArtifactError, 'anchor factor is unavailable'):
                self.plan(self.reader(days))

    def test_invalid_factor_or_provenance_rejects_instead_of_searching_back(self):
        for field, value in [('factor', v) for v in (0, -2, 'bad', float('nan'), float('inf'), True)] + [
                ('source_ref', ''), ('first_observed_at', 'invalid'), ('pit_qualification', 'invalid')]:
            reader = self.reader(('2020-08-26', '2020-08-27'))
            rows = list(reader.session_rows())
            rows[-1][field] = value
            reader.session_rows = Mock(return_value=iter(rows))
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ArtifactError, 'adjusted_price-600069.SH: invalid factor'):
                self.plan(reader)

    def test_unbound_source_ref_is_rejected(self):
        reader = self.reader()
        rows = list(reader.session_rows()); rows[0]['source_ref'] = 'unbound-source'
        reader.session_rows = Mock(return_value=iter(rows))
        with self.assertRaisesRegex(ArtifactError, 'source_ref outside DomainCommit Raw lineage'):
            self.plan(reader)

    def test_source_load_failure_retains_label(self):
        with self.assertRaisesRegex(ArtifactError, 'adjusted_price-600069.SH: raw content corrupt'):
            self.plan(self.reader(), raw_error=ArtifactError('raw content corrupt'))

    def test_observed_anchor_must_not_predate_its_raw_retrieval(self):
        reader = self.reader()
        rows = list(reader.session_rows())
        rows[0].update(pit_qualification='observed', availability_basis='first_observation',
                       first_observed_at='2020-08-27T12:00:00+08:00')
        reader.session_rows = Mock(return_value=iter(rows))
        with self.assertRaisesRegex(ArtifactError, 'first_observed_at predates its RawBatch retrieval'):
            self.plan(reader)

    def test_strict_explicit_anchor_does_not_search_back(self):
        spec = {'a': {'kind': 'adjusted_price', 'config': dict(symbols=['600069.SH'],
            start_session='2020-08-26', end_session='2020-08-27', anchor_session='2020-08-27',
            decision_cutoff='2020-08-27', pit_policy='strict_decision_time')}}
        with self.assertRaisesRegex(ArtifactError, 'anchor factor is unavailable'):
            resolve_price_anchors(self.reader(), spec, historical=True)
        spec['a']['config']['decision_cutoff'] = '2020-08-26'
        with self.assertRaisesRegex(ArtifactError, 'a: future adjusted anchor'):
            resolve_price_anchors(self.reader(), spec, historical=True)


class FixedArtifactScopeTest(unittest.TestCase):
    def test_in_scope_missing_states_are_preserved(self):
        view = FactView.__new__(FactView)
        view.reader = SimpleNamespace(commits={}, snapshot=SimpleNamespace(ref=SimpleNamespace(
            snapshot_id='fixed', manifest_digest='fixed-digest')))
        rows = tuple(dict(symbol='600069.SH', session=day, open=None, high=None, low=None,
                          close=None, adjustment_state=state) for day, state in
                     [('2020-08-26', 'no_market_price'), ('2020-08-27', 'missing_factor')])
        view.adjusted = SimpleNamespace(rows=rows, ref=SimpleNamespace(view_id='fixed-view'), manifest=dict(
            scope=dict(symbols=['600069.SH'], start_session='2020-08-26', end_session='2020-08-27'),
            domain_refs={}, anchor_session='2020-08-26', pit_policy='research_non_pit',
            pit_qualification='best_effort', decision_cutoff='2020-08-27'))
        result = view.read('adjusted_price', price_basis='anchor_adjusted')
        self.assertEqual(result['rows'], rows)
        self.assertEqual(result['pit_qualification'], 'best_effort')
        self.assertNotIn('ready_for_consumption', result)

    def test_existing_view_scope_defaults_subsets_and_rejections(self):
        run = json.loads(Path('deprecated/history/reports/pr7/run_manifest.json').read_bytes())
        root = fixture_root(run['source_root'])
        # Build only inside a disposable copy; existing immutable source stays untouched.
        with tempfile.TemporaryDirectory() as directory:
            root_copy = Path(directory) / 'data'; shutil.copytree(root, root_copy)
            config = dict(symbols=['688981.SH'], start_session='2025-06-10', end_session='2025-06-13',
                          anchor_session='2025-06-13', pit_policy='research_non_pit', decision_cutoff='2025-06-13')
            result = materialize_views(root_copy, run_id='scope', snapshot_id=run['refs']['snapshot_id'],
                                       views={'a': dict(kind='adjusted_price', config=config)})
            self.assertEqual(result['status'], 'VIEWS_BUILT')
            view = FactView(root_copy, run['refs']['snapshot_id'],
                            adjusted_price_view_id=result['published_views']['a']['view_id'])
            read = lambda **kwargs: view.read('adjusted_price', price_basis='anchor_adjusted', **kwargs)
            self.assertEqual(read()['rows'], read(symbols=config['symbols'],
                start_session=config['start_session'], end_session=config['end_session'])['rows'])
            self.assertEqual(len(read(start_session='2025-06-11', end_session='2025-06-11')['rows']), 1)
            for args in ({'start_session': '2025-06-01'}, {'end_session': '2025-06-30'},
                         {'start_session': '2025-06-13', 'end_session': '2025-06-10'},
                         {'symbols': ['600069.SH']}, {'fields': []}, {'fields': ['unknown']}):
                with self.subTest(args=args), self.assertRaises(ArtifactError):
                    read(**args)
            reader = SnapshotReader(root_copy, run['refs']['snapshot_id'])
            resolved, evidence = resolve_price_anchors(reader, {'a': dict(kind='adjusted_price', config=config)}, historical=True)
            self.assertEqual(resolved['a']['config'], config)
            self.assertEqual(evidence['anchors']['a']['actual_anchor_session'], '2025-06-13')
            # A preceding valid View must not publish before a later absent anchor is detected.
            bad = dict(config, anchor_session='2025-06-14', end_session='2025-06-14', decision_cutoff='2025-06-14')
            failed = materialize_views(root_copy, run_id='preflight', snapshot_id=run['refs']['snapshot_id'],
                views={'valid': dict(kind='market_replay', config={k: config[k] for k in ('symbols', 'start_session', 'end_session')}),
                       'bad': dict(kind='adjusted_price', config=bad)})
            self.assertEqual(failed['status'], 'FAILED')
            self.assertEqual(failed['published_views'], {})
            self.assertEqual(failed['failed']['bad']['stage'], 'price_anchor_preflight')
            self.assertIn('anchor factor is unavailable', failed['failed']['bad']['reason'])
            path = root_copy / 'canonical/adjustment_factors/commits' / reader.commits['adjustment_factors'].ref.commit_id / 'rows.json'
            path.chmod(0o600); path.write_bytes(path.read_bytes() + b' ')
            with self.assertRaises(ArtifactError):
                resolve_price_anchors(SnapshotReader(root_copy, run['refs']['snapshot_id']),
                                      {'a': dict(kind='adjusted_price', config=config)}, historical=True)
