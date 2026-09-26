import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import ArtifactError, SnapshotReader, materialize_views
from axiom_data import consumption, views, event_views, financial_views, financial_coverage
from fixture_locations import fixture_root


class ViewOperationBatchTest(unittest.TestCase):
    def setUp(self):
        # Internal publication/projection instrumentation; real public isolation has separate tests.
        self.enterContext(patch('axiom_data.frozen_execution.is_frozen', return_value=True))

    def test_market_replay_batch_rejects_changed_contract_before_next_build(self):
        run = json.loads(Path('reports/pr7/run_manifest.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'data'
            shutil.copytree(fixture_root(run['source_root']), root)
            reader = SnapshotReader(root, run['refs']['snapshot_id'])
            common = dict(start_session='2025-06-10', end_session='2025-06-13',
                          created_at='2026-09-24T00:00:00+08:00')
            configs = [dict(common, symbols=[symbol]) for symbol in ('600036.SH', '688981.SH')]
            with consumption.market_view_batch(reader, configs, 'market_replay'):
                views._build_market_replay_view(reader, **configs[0])
                commit = reader.commits['market_daily'].ref.commit_id
                contract = root / 'canonical/market_daily/commits' / commit / 'contract.json'
                contract.chmod(0o600)
                contract.write_bytes(contract.read_bytes() + b' ')
                with self.assertRaisesRegex(ArtifactError, 'Reader consumed input changed'):
                    views._build_market_replay_view(reader, **configs[1])
                self.assertTrue(reader._invalidated)

    def test_event_operation_rejects_changed_raw_request_before_next_builder(self):
        run = json.loads(Path('reports/pr7/run_manifest.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'data'
            shutil.copytree(fixture_root(run['source_root']), root)
            reader = SnapshotReader(root, run['refs']['snapshot_id'])
            common = dict(start_session='2025-06-10', end_session='2025-06-13',
                          pit_policy='best_effort_vendor_v1',
                          knowledge_cutoff='2025-06-13T23:59:59+08:00')
            symbols = ('600036.SH', '688981.SH')
            configs = [dict(common, symbols=[symbol]) for symbol in symbols]
            plan = {symbol: dict(kind='event_fact', config=config)
                    for symbol, config in zip(symbols, configs)}
            build = event_views.build_event_fact_view_from_reader
            calls = []
            def change_after_first(checked_reader, **config):
                calls.append(config['symbols'][0])
                ref = build(checked_reader, **config)
                if len(calls) == 1:
                    raw_id = reader.commits['margin_daily'].manifest['ordered_raw_batch_refs'][0]['raw_batch_id']
                    manifest = root / 'raw/batches' / raw_id / 'manifest.json'
                    manifest.chmod(0o600)
                    manifest.write_bytes(manifest.read_bytes() + b' ')
                return ref
            with patch('axiom_data.consumption.SnapshotReader', return_value=reader), \
                 patch('axiom_data.event_views.build_event_fact_view_from_reader',
                       side_effect=change_after_first), \
                 patch('axiom_data.event_views._project', wraps=event_views._project) as projection:
                result = materialize_views(root, run_id='event-changed-input',
                                           snapshot_id=run['refs']['snapshot_id'], views=plan)
            self.assertEqual(result['status'], 'FAILED')
            self.assertEqual(calls, list(symbols))
            self.assertEqual(projection.call_count, 1)
            self.assertIn(symbols[0], result['published_views'])
            self.assertNotIn(symbols[1], result['published_views'])
            self.assertTrue(reader._invalidated)

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
                self.assertEqual(sum(call.args[0] == 'market_daily' for call in reads.call_args_list), 2)
                if kind == 'market_replay':
                    for domain in ('security_status', 'price_limits'):
                        self.assertEqual(sum(call.args[0] == domain for call in reads.call_args_list), 2, reads.call_args_list)
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
                self.assertEqual(sum(call.args[0] == domain for call in reads.call_args_list), 2)
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
                       side_effect=AssertionError('completed builder entered')) as built, \
                 patch('axiom_data.event_views.project',
                       side_effect=AssertionError('completed projection entered')):
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
                 patch.object(batch_reader, 'session_rows', wraps=batch_reader.session_rows) as reads, \
                 patch.object(financial_coverage, 'admit_view', wraps=financial_coverage.admit_view) as admissions, \
                 patch.object(financial_views, 'active_members', wraps=financial_views.active_members) as membership:
                result = materialize_views(batch_root, run_id='financial-batch',
                                           snapshot_id=run['refs']['snapshot_id'], views=plan)
            self.assertEqual(result['status'], 'VIEWS_BUILT', result.get('failed'))
            self.assertEqual(admissions.call_count, 2)  # preflight + build; shared full-domain summary
            self.assertEqual(membership.call_count, len({
                (call.kwargs['group_id'], call.kwargs['target_session'])
                for call in membership.call_args_list}))
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
                       side_effect=AssertionError('completed builder entered')) as built, \
                 patch('axiom_data.financial_views.project',
                       side_effect=AssertionError('completed projection entered')):
                resumed = materialize_views(batch_root, run_id='financial-batch',
                                            snapshot_id=run['refs']['snapshot_id'], views=plan)
            self.assertEqual(resumed['status'], 'VIEWS_BUILT')
            self.assertEqual(built.call_count, 0)

    def test_current_fact_publication_projects_once_and_detects_damage(self):
        from axiom_data import ArtifactError
        run = json.loads(Path('reports/pr7/run_manifest.json').read_text())
        source = fixture_root(run['source_root'])
        old = json.loads((source / 'derived/pr6_fact/commits' /
                          run['refs']['pr6_view_id'] / 'manifest.json').read_bytes())
        cases = (
            (financial_views, financial_views.build_financial_fact_view_from_reader,
             financial_views.load_financial_fact_view_with_reader, 'pr6_fact',
             dict(symbols=['688981.SH'],start_session='2025-06-10',end_session='2025-06-13',
                  universe_ids=old['scope']['universe_ids'],industry_system=old['scope']['industry_system'],
                  pit_policy='best_effort_vendor_v1',knowledge_cutoff='2025-06-13T23:59:59+08:00')),
            (event_views, event_views.build_event_fact_view_from_reader,
             event_views.load_event_fact_view_with_reader, 'pr7_fact',
             dict(symbols=['688981.SH'],start_session='2025-06-10',end_session='2025-06-13',
                  pit_policy='best_effort_vendor_v1',knowledge_cutoff='2025-06-13T23:59:59+08:00')),
        )
        for module,builder,loader,kind,config in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)/'data';shutil.copytree(source,root)
                reader=SnapshotReader(root,run['refs']['snapshot_id'])
                with patch.object(module,'project',wraps=module.project) as projection:
                    ref=builder(reader,**config)
                    self.assertEqual(projection.call_count,1)
                    loader(root,ref.view_id,checked_reader=reader)
                    self.assertEqual(projection.call_count,1)
                rows=root/'derived'/kind/'commits'/ref.view_id/'states.json.gz'
                rows.write_bytes(rows.read_bytes()+b'corrupt')
                with self.assertRaises(ArtifactError):loader(root,ref.view_id,checked_reader=reader)
