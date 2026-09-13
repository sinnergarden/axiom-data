import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import date, timedelta

from axiom_data.artifacts import ArtifactError, _layout, _json_bytes, _digest, _validate_domain_rows
from axiom_data.contracts import load_contract
from axiom_data.partition_rows import PartitionRows, array_rows
from axiom_data.partitions import POLICY, publish_partitions


from test_artifacts import synthetic_source_fixture


@synthetic_source_fixture
class PartitionStreamTest(unittest.TestCase):
    def test_published_v2_loader_dispatch_and_exact_legacy_values(self):
        from test_artifacts import build_pack
        from axiom_data import BuildApplication, MarketDomainBuilder, load_domain_commit
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = build_pack(root)
            builder = MarketDomainBuilder(root, 'market_daily',
                calendar_commit_id=pack['calendar'].commit_id,
                security_master_commit_id=pack['security'].commit_id,
                builder_config={'storage_policy': 'domain_time_blocks.v1'})
            ref = BuildApplication('market_daily', builder).build(None, ['raw-market'], [], 'market_daily.v1')
            with patch('axiom_data.partition_rows.STREAM_ROW_THRESHOLD', 1):
                current = load_domain_commit(root, 'market_daily', ref.commit_id)
                self.assertIsInstance(current.rows, PartitionRows)
                legacy = load_domain_commit(root, 'market_daily', pack['market'].commit_id)
                self.assertEqual(current.rows, legacy.rows)
                self.assertEqual(current.manifest['logical_content_digest'], legacy.manifest['logical_content_digest'])

    def test_json_chunk_boundaries_and_malformed_arrays(self):
        rows = [{'text': '特钢Ⅲ\\"\n', 'nested': [None, True, {'x': 2.5}]}] * 3
        for size in (1, 2, 7, 16384):
            self.assertEqual(list(array_rows(io.StringIO(_json_bytes(rows).decode()), size)), rows)
        for text in ('{}', '[{}', '[{},]', '[{},{}]x', '[{}{}]', '[null]', '[{"x":]'):
            with self.subTest(text=text), self.assertRaises(ArtifactError):
                list(array_rows(io.StringIO(text), 2))

    def test_streamed_digest_keys_counts_and_mutation_fail_closed(self):
        rows = [dict(session=(date(2024, 1, 1) + timedelta(days=i)).isoformat(),
            symbol='600000.SH', open=1., high=1., low=1., close=1., pre_close=1.,
            volume_shares=100, amount_cny=100., adj_factor=1., up_limit=1.1,
            down_limit=.9, is_suspended=False, turnover_rate=1.,
            total_market_cap_cny=100., circulating_market_cap_cny=100.) for i in range(4200)]
        with tempfile.TemporaryDirectory() as root:
            layout = _layout(root)
            entries = publish_partitions(layout, 'market_daily', rows)
            manifest = dict(partition_policy=POLICY, output_files=[], partitions=entries,
                            logical_content_digest=_digest(_json_bytes(rows)))
            def sequence(m=manifest):
                return PartitionRows(layout, 'market_daily', m, load_contract('market_daily.v1'))
            stream = sequence()
            _validate_domain_rows('market_daily', stream)
            self.assertEqual(stream, rows)
            self.assertEqual(stream[4095:4098], tuple(rows[4095:4098]))
            self.assertEqual(stream[-1], rows[-1])
            for key, value in [('rows', entries[0]['rows'] + 1), ('bytes', entries[0]['bytes'] + 1),
                               ('key', '1900-01')]:
                bad = copy.deepcopy(manifest)
                bad['partitions'][0][key] = value
                with self.subTest(key=key), self.assertRaises(ArtifactError):
                    _validate_domain_rows('market_daily', sequence(bad))
            bad = dict(manifest, logical_content_digest='sha256:' + '0' * 64)
            with self.assertRaises(ArtifactError):
                _validate_domain_rows('market_daily', sequence(bad))
            path = layout.domain_objects('market_daily') / entries[-1]['object_id'] / 'rows.json'
            path.write_bytes(b'[]')
            with self.assertRaisesRegex(ArtifactError, 'partition content digest'):
                _validate_domain_rows('market_daily', stream)
