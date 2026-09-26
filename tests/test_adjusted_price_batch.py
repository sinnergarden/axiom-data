import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import ArtifactError, SnapshotReader, materialize_views
from axiom_data import views
from fixture_locations import fixture_root


FIXTURE = fixture_root('/home/liuming/workspace/axiom/data/forensic/pr5-dm1-market-reference-20260906-b1b6-r4')
SNAPSHOT = 'snapshot-b1303edb81a3e7b48f38909ebd52ae4e5512030679a3eadfb8ace7c9c91de842'
START = '2025-06-01'
END = '2025-09-05'
FIXED_TIME = '2026-09-23T00:00:00+08:00'


class AdjustedPriceBatchTest(unittest.TestCase):
    def test_serial_and_operation_batch_publish_the_same_independent_views(self):
        symbols = ('600000.SH', '600036.SH')
        with tempfile.TemporaryDirectory() as directory:
            serial_root = Path(directory) / 'serial'
            batch_root = Path(directory) / 'batch'
            shutil.copytree(FIXTURE, serial_root)
            shutil.copytree(FIXTURE, batch_root)
            configs = [dict(symbols=[symbol], start_session=START, end_session=END,
                            anchor_session=END, pit_policy='research_non_pit',
                            decision_cutoff=END, created_at=FIXED_TIME)
                       for symbol in symbols]
            configs[1].update(end_session='2025-09-04', anchor_session='2025-09-04',
                              decision_cutoff='2025-09-04')
            serial_reader = SnapshotReader(serial_root, SNAPSHOT)
            serial = [views._build_adjusted_price_view(serial_reader, **config)
                      for config in configs]
            plan = {symbol: {'kind': 'adjusted_price', 'config': config}
                    for symbol, config in zip(symbols, configs)}
            batch_reader = SnapshotReader(batch_root, SNAPSHOT)
            with patch('axiom_data.consumption.SnapshotReader', return_value=batch_reader), \
                 patch.object(batch_reader, 'session_rows', wraps=batch_reader.session_rows) as reads:
                result = materialize_views(batch_root, run_id='adjusted-batch',
                                           snapshot_id=SNAPSHOT, views=plan)
            self.assertEqual(result['status'], 'VIEWS_BUILT', result.get('failed'))
            source_reads = [call.args[0] for call in reads.call_args_list
                            if call.args[0] in {'market_daily', 'adjustment_factors'}]
            self.assertEqual(source_reads.count('market_daily'), 1)
            self.assertEqual(source_reads.count('adjustment_factors'), 2)  # shared preflight + bounded build
            for symbol, expected in zip(symbols, serial):
                actual = result['published_views'][symbol]
                self.assertEqual(actual['view_id'], expected.view_id)
                self.assertEqual(actual['manifest_digest'], expected.manifest_digest)
                serial_path = serial_root / 'derived/adjusted_price/commits' / expected.view_id
                batch_path = batch_root / 'derived/adjusted_price/commits' / expected.view_id
                self.assertEqual((serial_path / 'rows.json').read_bytes(),
                                 (batch_path / 'rows.json').read_bytes())
                self.assertEqual(json.loads((serial_path / 'manifest.json').read_bytes()),
                                 json.loads((batch_path / 'manifest.json').read_bytes()))
            with patch('axiom_data.views._build_adjusted_price_view',
                       side_effect=AssertionError('completed builder entered')) as builder:
                resumed = materialize_views(batch_root, run_id='adjusted-batch',
                                            snapshot_id=SNAPSHOT, views=plan)
            self.assertEqual(resumed['published_views'], result['published_views'])
            self.assertEqual(builder.call_count, 0)
            missing = result['published_views'][symbols[0]]['view_id']
            missing_path = batch_root / 'derived/adjusted_price/commits' / missing
            missing_path.chmod(0o755)
            shutil.rmtree(missing_path)
            with patch('axiom_data.views._build_adjusted_price_view',
                       wraps=views._build_adjusted_price_view) as builder:
                rebuilt = materialize_views(batch_root, run_id='adjusted-batch',
                                            snapshot_id=SNAPSHOT, views=plan)
            self.assertEqual(rebuilt['status'], 'VIEWS_BUILT')
            self.assertEqual(rebuilt['published_views'], result['published_views'])
            self.assertEqual(builder.call_count, 1)

    def test_batch_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'data'
            shutil.copytree(FIXTURE, root)
            reader = SnapshotReader(root, SNAPSHOT)
            config = dict(symbols=['600000.SH'], start_session=START, end_session=END)
            with self.assertRaisesRegex(ArtifactError, 'batch exceeds'):
                with views.adjusted_price_batch(reader, [config] * 51):
                    pass
            with views.adjusted_price_batch(reader, [config]):
                pass

    def test_source_replacement_during_batch_is_rejected(self):
        from axiom_data.artifacts import _safe_path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.json'
            source.write_text('original')

            class Reader:
                def session_rows(self, domain, start, end):
                    _safe_path(root, source, closure=root)
                    return iter(({'symbol': '600000.SH', 'session': START},))

            config = dict(symbols=['600000.SH'], start_session=START, end_session=END)
            with self.assertRaisesRegex(ArtifactError, 'source changed during batch'):
                with views.adjusted_price_batch(Reader(), [config]):
                    source.write_text('replaced')


if __name__ == '__main__':
    unittest.main()
