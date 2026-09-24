import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import SnapshotReader, materialize_views
from axiom_data import consumption, views, event_views, financial_views
from fixture_locations import fixture_root


class ViewOperationBatchTest(unittest.TestCase):
    def test_public_batch_matches_serial_artifacts_and_resume(self):
        run = json.loads(Path('reports/pr7/run_manifest.json').read_text())
        symbols = ('600036.SH', '688981.SH')
        common = dict(start_session='2025-06-10', end_session='2025-06-13',
                      created_at='2026-09-24T00:00:00+08:00')
        for kind, builder, path in (
            ('market_replay', views._build_market_replay_view, 'derived/market_replay/commits'),
            ('market_qlib', consumption._build_qlib_view, 'exports/qlib'),
        ):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                serial_root, batch_root = (Path(directory) / name for name in ('serial', 'batch'))
                shutil.copytree(fixture_root(run['source_root']), serial_root)
                shutil.copytree(fixture_root(run['source_root']), batch_root)
                configs = [dict(common, symbols=[symbol]) for symbol in symbols]
                if kind == 'market_qlib':
                    for config in configs:
                        config['fields'] = ['close']
                serial_reader = SnapshotReader(serial_root, run['refs']['snapshot_id'])
                expected = [builder(serial_reader, **config) for config in configs]
                plan = {symbol: dict(kind=kind, config=config)
                        for symbol, config in zip(symbols, configs)}
                batch_reader = SnapshotReader(batch_root, run['refs']['snapshot_id'])
                with patch('axiom_data.consumption.SnapshotReader', return_value=batch_reader), \
                     patch.object(batch_reader, 'session_rows', wraps=batch_reader.session_rows) as reads:
                    result = materialize_views(batch_root, run_id='market-batch',
                                               snapshot_id=run['refs']['snapshot_id'], views=plan)
                self.assertEqual(result['status'], 'VIEWS_BUILT', result.get('failed'))
                self.assertEqual(sum(call.args[0] == 'market_daily' for call in reads.call_args_list), 1)
                if kind == 'market_replay':
                    for domain in ('security_status', 'price_limits'):
                        self.assertEqual(sum(call.args[0] == domain for call in reads.call_args_list), 1, reads.call_args_list)
                for symbol, ref in zip(symbols, expected):
                    self.assertEqual(result['published_views'][symbol]['view_id'], ref.view_id)
                    left = serial_root / path / ref.view_id
                    right = batch_root / path / ref.view_id
                    self.assertEqual((left / 'manifest.json').read_bytes(), (right / 'manifest.json').read_bytes())
                    for file in left.rglob('*'):
                        if file.is_file():
                            self.assertEqual(file.read_bytes(), (right / file.relative_to(left)).read_bytes())
                target = 'axiom_data.views._build_market_replay_view' if kind == 'market_replay' else 'axiom_data.consumption._build_qlib_view'
                with patch(target, side_effect=AssertionError('completed builder entered')) as built:
                    resumed = materialize_views(batch_root, run_id='market-batch',
                                                snapshot_id=run['refs']['snapshot_id'], views=plan)
                self.assertEqual(resumed['status'], 'VIEWS_BUILT')
                self.assertEqual(built.call_count, 0)

    def test_event_batch_matches_serial_artifacts_and_resume(self):
        run = json.loads(Path('reports/pr7/run_manifest.json').read_text())
        symbols = ('600036.SH', '688981.SH')
        common = dict(start_session='2025-06-10', end_session='2025-06-13',
                      pit_policy='best_effort_vendor_v1',
                      knowledge_cutoff='2025-06-13T23:59:59+08:00')
        with tempfile.TemporaryDirectory() as directory:
            serial_root, batch_root = (Path(directory) / name for name in ('serial', 'batch'))
            shutil.copytree(fixture_root(run['source_root']), serial_root)
            shutil.copytree(fixture_root(run['source_root']), batch_root)
            configs = [dict(common, symbols=[symbol]) for symbol in symbols]
            serial_reader = SnapshotReader(serial_root, run['refs']['snapshot_id'])
            expected = [event_views.build_event_fact_view_from_reader(serial_reader, **config)
                        for config in configs]
            plan = {symbol: dict(kind='event_fact', config=config)
                    for symbol, config in zip(symbols, configs)}
            batch_reader = SnapshotReader(batch_root, run['refs']['snapshot_id'])
            with patch('axiom_data.consumption.SnapshotReader', return_value=batch_reader), \
                 patch.object(batch_reader, 'session_rows', wraps=batch_reader.session_rows) as reads:
                result = materialize_views(batch_root, run_id='event-batch',
                                           snapshot_id=run['refs']['snapshot_id'], views=plan)
            self.assertEqual(result['status'], 'VIEWS_BUILT', result.get('failed'))
            for domain in ('margin_daily', 'moneyflow_daily'):
                self.assertEqual(sum(call.args[0] == domain for call in reads.call_args_list), 1)
            for symbol, ref in zip(symbols, expected):
                self.assertEqual(result['published_views'][symbol]['view_id'], ref.view_id)
                left = serial_root / 'derived/pr7_fact/commits' / ref.view_id
                right = batch_root / 'derived/pr7_fact/commits' / ref.view_id
                for file in left.rglob('*'):
                    if file.is_file():
                        if file.name == 'manifest.json':
                            a = json.loads(file.read_bytes()); b = json.loads((right / file.relative_to(left)).read_bytes())
                            a.pop('created_at'); b.pop('created_at')
                            self.assertEqual(a, b)
                        elif file.name != 'manifest.sha256':
                            self.assertEqual(file.read_bytes(), (right / file.relative_to(left)).read_bytes())
            with patch('axiom_data.event_views.build_event_fact_view_from_reader',
                       side_effect=AssertionError('completed builder entered')) as built:
                resumed = materialize_views(batch_root, run_id='event-batch',
                                            snapshot_id=run['refs']['snapshot_id'], views=plan)
            self.assertEqual(resumed['status'], 'VIEWS_BUILT')
            self.assertEqual(built.call_count, 0)

    def test_financial_batch_matches_serial_artifacts_and_resume(self):
        run = json.loads(Path('reports/pr7/run_manifest.json').read_text())
        symbols = ('600036.SH', '688981.SH')
        source = fixture_root(run['source_root'])
        old = json.loads((source / 'derived/pr6_fact/commits' /
                          run['refs']['pr6_view_id'] / 'manifest.json').read_bytes())
        common = dict(start_session='2025-06-10', end_session='2025-06-13',
                      pit_policy='best_effort_vendor_v1',
                      knowledge_cutoff='2025-06-13T23:59:59+08:00',
                      universe_ids=old['scope']['universe_ids'],
                      industry_system=old['scope']['industry_system'])
        with tempfile.TemporaryDirectory() as directory:
            serial_root, batch_root = (Path(directory) / name for name in ('serial', 'batch'))
            shutil.copytree(source, serial_root)
            shutil.copytree(source, batch_root)
            configs = [dict(common, symbols=[symbol]) for symbol in symbols]
            serial_reader = SnapshotReader(serial_root, run['refs']['snapshot_id'])
            expected = [financial_views.build_financial_fact_view_from_reader(serial_reader, **config)
                        for config in configs]
            plan = {symbol: dict(kind='financial_fact', config=config)
                    for symbol, config in zip(symbols, configs)}
            batch_reader = SnapshotReader(batch_root, run['refs']['snapshot_id'])
            with patch('axiom_data.consumption.SnapshotReader', return_value=batch_reader), \
                 patch.object(batch_reader, 'session_rows', wraps=batch_reader.session_rows) as reads:
                result = materialize_views(batch_root, run_id='financial-batch',
                                           snapshot_id=run['refs']['snapshot_id'], views=plan)
            self.assertEqual(result['status'], 'VIEWS_BUILT', result.get('failed'))
            valuation_reads = [call for call in reads.call_args_list if call.args[0] == 'valuation_daily']
            self.assertEqual(len(valuation_reads), 1, valuation_reads)
            for symbol, ref in zip(symbols, expected):
                self.assertEqual(result['published_views'][symbol]['view_id'], ref.view_id)
                left = serial_root / 'derived/pr6_fact/commits' / ref.view_id
                right = batch_root / 'derived/pr6_fact/commits' / ref.view_id
                for file in left.rglob('*'):
                    if file.is_file():
                        if file.name == 'manifest.json':
                            a = json.loads(file.read_bytes()); b = json.loads((right / file.relative_to(left)).read_bytes())
                            a.pop('created_at'); b.pop('created_at')
                            self.assertEqual(a, b)
                        elif file.name != 'manifest.sha256':
                            self.assertEqual(file.read_bytes(), (right / file.relative_to(left)).read_bytes())
            with patch('axiom_data.financial_views.build_financial_fact_view_from_reader',
                       side_effect=AssertionError('completed builder entered')) as built:
                resumed = materialize_views(batch_root, run_id='financial-batch',
                                            snapshot_id=run['refs']['snapshot_id'], views=plan)
            self.assertEqual(resumed['status'], 'VIEWS_BUILT')
            self.assertEqual(built.call_count, 0)
