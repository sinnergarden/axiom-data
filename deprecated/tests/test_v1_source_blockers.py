"""Real source counterexamples retain the accepted fail-closed behavior."""
import json
from pathlib import Path
import tempfile
import unittest

from axiom_data import (
    ArtifactError, BuildApplication, TushareCollector, TushareMarketBuilder,
    load_raw_batch,
)
from axiom_data.contracts import load_contract
from axiom_data.fundamentals_source import FundamentalsBuilder, FundamentalsCollector


FIXTURE = Path(__file__).parent / 'fixtures/v1_source_blockers.json'


class FrozenRowClient:
    def __init__(self, row):
        self.row = row

    def query(self, endpoint, *, fields, **params):
        return [{field: self.row.get(field) for field in fields.split(',')}]


class SourceBlockerTest(unittest.TestCase):
    def test_real_delisted_security_cannot_publish_unverified_boundary(self):
        fixture = json.loads(FIXTURE.read_bytes())
        row = fixture['delisted_row']
        self.assertEqual((row['ts_code'], row['delist_date']), ('000005.SZ', '20240426'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = TushareCollector(root, FrozenRowClient(row)).collect(
                'stock_basic', {'exchange': '', 'list_status': 'D'},
                retrieved_at=fixture['security_retrieved_at'],
            )
            builder = TushareMarketBuilder(root, 'security_master', builder_config={
                'symbols': [row['ts_code']], 'start_session': '2014-01-01',
                'end_session': '2026-09-08',
            })
            with self.assertRaisesRegex(ArtifactError, 'unverified Tushare delist_date boundary'):
                BuildApplication('security_master', builder).build(
                    None, [raw.raw_batch_id], [], 'security_master.v2',
                )
            self.assertEqual(json.loads(load_raw_batch(root, raw.raw_batch_id).payload), [row])
            self.assertFalse(list((root / 'canonical').glob('security_master/commits/*/manifest.json')))
            self.assertFalse((root / 'current.json').exists())

    def test_real_listing_day_null_industry_is_not_a_classification(self):
        fixture = json.loads(FIXTURE.read_bytes())['null_industry']
        row = fixture['source_row']
        self.assertEqual(row['ts_code'], '001289.SZ')
        self.assertEqual(row['trade_date'], fixture['stock_basic_row']['list_date'])
        self.assertIsNone(row['industry'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ref = FundamentalsCollector(root, FrozenRowClient(row)).collect(
                'bak_basic', {'trade_date': row['trade_date']},
                retrieved_at=fixture['retrieved_at'], profile_version='tushare_fundamentals.v2',
            )
            raw = load_raw_batch(root, ref.raw_batch_id)
            builder = FundamentalsBuilder(root, 'industry_membership',
                                 builder_config={'symbols': [row['ts_code']]})
            with self.assertRaisesRegex(ArtifactError, 'industry classification missing'):
                builder._build_rows(load_contract('industry_membership.v2'), [], [raw])
            self.assertEqual(json.loads(raw.payload), [row])
