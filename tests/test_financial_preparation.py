"""Execution reuse keeps complete-scope admission and immutable-input checks."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from axiom_data import ArtifactError, SnapshotReader
from axiom_data.artifacts import _json_bytes
from axiom_data import pr6_coverage as coverage
from axiom_data.pr6_views import project
from fixture_locations import fixture_root


class FinancialPreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'data'
        run = json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        shutil.copytree(fixture_root(run['source_root']), self.root)
        self.reader = SnapshotReader(self.root, run['refs']['snapshot_id'])
        view = self.root / 'derived/pr6_fact/commits' / run['refs']['pr6_view_id']
        self.manifest = json.loads((view / 'manifest.json').read_bytes())
        self.expected = (view / 'rows.json').read_bytes()

    def project(self, scope=None, resolution=False):
        m = self.manifest
        return project(self.reader, scope or m['scope'], m['pit_policy'],
                       m['knowledge_cutoff'], financial_resolution=resolution)

    def test_exact_payload_and_scope_independent_admission_reuse(self):
        with patch.object(coverage, 'select_revisions', wraps=coverage.select_revisions) as selector:
            first = self.project()
            financial_calls = lambda: sum(c.args[0] is self.reader.commits['financial_events'].rows
                                          for c in selector.call_args_list)
            self.assertEqual(financial_calls(), 1)
            self.assertEqual(_json_bytes(first), self.expected)
            # Caller edits cannot change cached admission/history.
            first['actual_available_scope']['financial_report_periods'].clear()
            self.assertEqual(_json_bytes(self.project()), self.expected)
            subset = dict(self.manifest['scope'], symbols=[self.manifest['scope']['symbols'][0]])
            self.project(subset)
            self.assertEqual(financial_calls(), 1)
            self.assertLessEqual(sum(len(v[1]) for v in self.reader._financial_preparation.values()),
                                 64 * 1024 * 1024)

    def test_new_commit_object_cannot_hide_out_of_scope_conflict(self):
        from axiom_data.domains.market import MarketContractError
        self.project()
        scope = dict(self.manifest['scope'], symbols=[self.manifest['scope']['symbols'][0]])
        commit = self.reader.commits['financial_events']
        rows = list(commit.rows)
        source = next(r for r in rows if r['symbol'] not in scope['symbols'])
        conflict = copy.deepcopy(source)
        conflict['revision_id'] = 'outside-request-conflict'
        conflict['values'][next(iter(conflict['values']))] = 123456
        self.reader.commits['financial_events'] = replace(commit, rows=tuple([*rows, conflict]))
        with self.assertRaisesRegex(MarketContractError, 'ambiguous simultaneous'):
            self.project(scope)
        current = self.project(scope, resolution=True)
        self.assertTrue(any(r['symbol'] == source['symbol'] and r['ambiguous_fields']
                            for r in current['actual_available_scope']['financial_ambiguities']))

    def test_changed_file_and_ancestor_are_rejected_before_reuse(self):
        self.project()
        commit = self.reader.commits['financial_events']
        path = self.root / 'canonical/financial_events/commits' / commit.ref.commit_id / 'rows.json'
        original = path.read_bytes()
        path.chmod(0o600); path.write_bytes(original + b' ')
        with self.assertRaisesRegex(ArtifactError, 'changed after validation'):
            self.project()
        path.write_bytes(original)
        self.reader = SnapshotReader(self.root, self.reader.snapshot.ref.snapshot_id)
        self.project()
        moved = self.root.with_name('moved')
        self.root.rename(moved); self.root.symlink_to(moved, target_is_directory=True)
        try:
            with self.assertRaisesRegex(ArtifactError, 'changed after validation'):
                self.project()
        finally:
            self.root.unlink(); moved.rename(self.root)

    def test_cutoff_and_resolution_are_distinct_admission_inputs(self):
        self.project()
        m = self.manifest
        with patch.object(coverage, 'select_financial_revisions', wraps=coverage.select_financial_revisions) as select:
            self.project(resolution=True)
            self.assertEqual(select.call_count, 1)
            self.project(resolution=True)
            self.assertEqual(select.call_count, 1)
            coverage._admission_inputs(self.reader, m['pit_policy'], '2025-06-12T23:59:59+08:00', True)
            self.assertEqual(select.call_count, 2)

    def test_sparse_valuation_hole_does_not_become_complete_interval(self):
        commit = self.reader.commits['valuation_daily']
        m = self.manifest
        first = self.project()
        day = first['sessions'][1]; symbol = m['scope']['symbols'][0]
        rows = tuple(r for r in commit.rows if (r['symbol'], r['session']) != (symbol, day))
        self.reader.commits['valuation_daily'] = replace(commit, rows=rows)
        with patch.object(coverage,'select_revisions',wraps=coverage.select_revisions) as select:
            with self.assertRaisesRegex(ArtifactError, 'valuation date/security gap'):
                self.project()
            self.assertFalse(any(c.args[0] is self.reader.commits['financial_events'].rows
                                 for c in select.call_args_list))

    def test_dependency_validation_traverses_once_and_keeps_error_precedence(self):
        from axiom_data.artifacts import _validate_pr6_dependencies
        class Rows:
            calls = 0
            values = [{'symbol':'600000.SH', 'session':'2025-06-10'}]
            def __iter__(self):
                self.calls += 1
                return iter(self.values)
        rows = Rows()
        dependencies = {'security_master':SimpleNamespace(rows=[{'symbol':'600000.SH','exchange':'SSE'}]),
                        'trading_calendar':SimpleNamespace(rows=[{'exchange':'SSE','session':'2025-06-10','is_open':True}])}
        for domain in ('valuation_daily','margin_daily','moneyflow_daily'):
            rows.calls = 0
            _validate_pr6_dependencies(domain,rows,dependencies)
            self.assertEqual(rows.calls,1)
        rows.values = [{'symbol':'600000.SH','session':'2025-06-11'}]
        with self.assertRaisesRegex(ArtifactError,'outside open calendar'):
            _validate_pr6_dependencies('margin_daily',rows,dependencies)
        rows.values.append({'symbol':'unknown','session':'2025-06-10'})
        with self.assertRaisesRegex(ArtifactError,'no security identity'):
            _validate_pr6_dependencies('margin_daily',rows,dependencies)

    def test_first_view_publication_keeps_source_validation_reusable(self):
        from axiom_data.pr6_views import _build_pr6_fact_view, _load_pr6_fact_view
        for directory in (self.root / 'derived').rglob('*'):
            if directory.is_dir():
                directory.chmod(0o755)
        shutil.rmtree(self.root / 'derived')
        reader = SnapshotReader(self.root, self.reader.snapshot.ref.snapshot_id)
        config = dict(self.manifest['scope'], pit_policy=self.manifest['pit_policy'],
                      knowledge_cutoff=self.manifest['knowledge_cutoff'])
        ref = _build_pr6_fact_view(reader, **config)
        self.assertEqual(_load_pr6_fact_view(self.root, ref.view_id, checked_reader=reader).ref, ref)

    def test_reader_preparation_rejects_changed_implementation(self):
        source = Path(self.temp.name) / 'code'
        source.mkdir()
        implementation = source / 'projection.py'
        implementation.write_text('revision = 1')
        with patch('axiom_data.consumption.files', return_value=source):
            self.reader = SnapshotReader(self.root, self.reader.snapshot.ref.snapshot_id)
        self.project()
        implementation.write_text('revision = 2')
        with self.assertRaisesRegex(ArtifactError,'changed after validation'):
            self.project()

    def test_batch_matches_serial_and_prepares_history_and_calendar_once(self):
        scopes = [dict(self.manifest['scope'], symbols=[symbol])
                  for symbol in self.manifest['scope']['symbols']]
        expected = [_json_bytes(self.project(scope, resolution=True)) for scope in scopes]
        self.reader._financial_preparation = {}
        with coverage.financial_batch(self.reader), patch.object(
                self.reader, 'facts', wraps=self.reader.facts) as facts, patch.object(
                self.reader, 'trading_calendar', wraps=self.reader.trading_calendar) as calendar, patch.object(
                coverage, 'select_financial_revisions', wraps=coverage.select_financial_revisions) as admission:
            for scope, content in zip(scopes, expected):
                actual = self.project(scope, resolution=True)
                self.assertEqual(_json_bytes(actual), content)
                actual['events'].clear()
                actual['actual_available_scope'].clear()
            for scope, content in zip(scopes, expected):
                self.assertEqual(_json_bytes(self.project(scope, resolution=True)), content)
            self.assertEqual(sum(c.args == ('financial_events',) for c in facts.call_args_list), 1)
            self.assertEqual(calendar.call_count, 1)
            self.assertEqual(admission.call_count, 1)
        self.assertIsNone(self.reader._financial_batch)

    def test_batch_preserves_scope_wide_conflict_and_changed_input_rejection(self):
        with coverage.financial_batch(self.reader):
            self.test_new_commit_object_cannot_hide_out_of_scope_conflict()
        with coverage.financial_batch(self.reader):
            self.project(resolution=True)
            commit = self.reader.commits['financial_events']
            path = self.root / 'canonical/financial_events/commits' / commit.ref.commit_id / 'rows.json'
            path.chmod(0o600)
            path.write_bytes(path.read_bytes() + b' ')
            with self.assertRaisesRegex(ArtifactError, 'changed after validation'):
                self.project(resolution=True)

    def test_batch_publication_identity_and_completed_resume(self):
        from axiom_data import materialize_views
        from axiom_data.pr6_views import _build_pr6_fact_view
        m = self.manifest
        configs = [dict(m['scope'], symbols=[symbol], pit_policy=m['pit_policy'],
                        knowledge_cutoff=m['knowledge_cutoff']) for symbol in m['scope']['symbols']]
        expected = [_build_pr6_fact_view(self.reader, **config) for config in configs]
        views = {str(i):dict(kind='pr6_fact', config=config) for i,config in enumerate(configs)}
        args = dict(run_id='financial-batch', snapshot_id=self.reader.snapshot.ref.snapshot_id, views=views)
        result = materialize_views(self.root, **args)
        self.assertEqual(result['status'], 'VIEWS_BUILT', result.get('failed'))
        for i,ref in enumerate(expected):
            self.assertEqual(result['published_views'][str(i)]['view_id'], ref.view_id)
            self.assertEqual(result['published_views'][str(i)]['manifest_digest'], ref.manifest_digest)
        with patch('axiom_data.financial_views.build_financial_fact_view_from_reader') as builder:
            resumed = materialize_views(self.root, **args)
        self.assertEqual(resumed['status'], 'VIEWS_BUILT', resumed.get('failed'))
        self.assertEqual(builder.call_count, 0)
        target = self.root / 'derived/pr6_fact/commits' / expected[0].view_id
        for path in target.rglob('*'):
            if path.is_dir():
                path.chmod(0o755)
        target.chmod(0o755)
        shutil.rmtree(target)
        with patch('axiom_data.financial_views.build_financial_fact_view_from_reader', wraps=_build_pr6_fact_view) as builder:
            rebuilt = materialize_views(self.root, **args)
        self.assertEqual(rebuilt['status'], 'VIEWS_BUILT', rebuilt.get('failed'))
        self.assertEqual(builder.call_count, 1)
        payload = target / 'states.json.gz'
        payload.chmod(0o600)
        payload.write_bytes(b'corrupt')
        with patch('axiom_data.financial_views.build_financial_fact_view_from_reader', wraps=_build_pr6_fact_view) as builder:
            corrupt = materialize_views(self.root, **args)
        self.assertEqual(corrupt['status'], 'FAILED')
        self.assertEqual(builder.call_count, 1)
        self.assertNotIn('0', corrupt['published_views'])

    def test_batch_interruption_resumes_only_remaining_artifacts(self):
        from axiom_data import materialize_views
        from axiom_data.pr6_views import _build_pr6_fact_view
        m = self.manifest
        configs = [dict(m['scope'], symbols=[symbol], pit_policy=m['pit_policy'],
                        knowledge_cutoff=m['knowledge_cutoff']) for symbol in m['scope']['symbols']]
        self.assertGreaterEqual(len(configs), 2)
        views = {str(i):dict(kind='pr6_fact', config=config) for i,config in enumerate(configs)}
        args = dict(run_id='financial-interrupted', snapshot_id=self.reader.snapshot.ref.snapshot_id, views=views)
        calls = 0
        def interrupt(reader, **config):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt()
            return _build_pr6_fact_view(reader, **config)
        with patch('axiom_data.financial_views.build_financial_fact_view_from_reader', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                materialize_views(self.root, **args)
        with patch('axiom_data.financial_views.build_financial_fact_view_from_reader', wraps=_build_pr6_fact_view) as builder:
            result = materialize_views(self.root, **args)
        self.assertEqual(result['status'], 'VIEWS_BUILT', result.get('failed'))
        self.assertEqual(builder.call_count, len(configs) - 1)

    def test_batch_still_rejects_valuation_gap_before_financial_preparation(self):
        with coverage.financial_batch(self.reader), patch.object(
                self.reader, 'facts', wraps=self.reader.facts) as facts:
            self.test_sparse_valuation_hole_does_not_become_complete_interval()
            # The first valid request prepares once; the changed, invalid
            # scope must fail without reading a replacement financial history.
            self.assertEqual(sum(c.args == ('financial_events',) for c in facts.call_args_list), 1)


if __name__ == '__main__':
    unittest.main()
