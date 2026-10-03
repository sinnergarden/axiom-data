"""Fact dictionary scope, native units and unmapped Raw remain distinct."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import Data, QueryError
from axiom_data.provider_local import CONTRACTS, profile_for


class DictionaryTests(unittest.TestCase):
    def test_catalogue_has_native_units_and_does_not_create_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'absent'
            result = Data(root).dictionary(domains=('market_daily', 'financial_events'))
            fields = result['fields']
            stock = next(r for r in fields if r['endpoint'] == 'daily' and r['canonical_field'] == 'volume_shares')
            fund = next(r for r in fields if r['endpoint'] == 'fund_daily' and r['canonical_field'] == 'volume_units')
            self.assertEqual((stock['raw_field'], stock['raw_unit'], stock['unit'], stock['conversion']['factor']),
                             ('vol', 'hundred-share lots', 'shares', '100'))
            self.assertEqual(fund['unit'], 'fund units')
            self.assertIsNone(stock['snapshot_available'])
            self.assertFalse(root.exists())

    def test_snapshot_dictionary_reads_declaration_without_scanning_facts(self):
        data = Data('/dictionary-does-not-write')
        profile = profile_for('daily', identity_map={})
        manifest = {'domains': {'market_daily': {'contract': CONTRACTS['daily'], 'source_profile': profile}}}
        with patch.object(data.store, 'load_snapshot', return_value=manifest), \
             patch.object(data.store, 'read_partition', side_effect=AssertionError('unexpected fact scan')):
            result = data.dictionary(snapshot='fixed-snapshot', domains=('market_daily', 'financial_events'))
        close = next(r for r in result['fields'] if r['endpoint'] == 'daily' and r['canonical_field'] == 'close')
        revenue = next(r for r in result['fields'] if r['canonical_field'] == 'total_revenue')
        self.assertTrue(close['snapshot_available'])
        self.assertFalse(revenue['snapshot_available'])
        with self.assertRaises(QueryError):
            data.dictionary(snapshot='current')

    def test_only_explicit_raw_is_inspected_and_extra_has_no_reader_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Data(tmp)
            raw = data.store.write_raw(json.dumps([{'ts_code': '000001.SZ', 'trade_date': '20260831',
                'vendor_extra': 7}]).encode(), domain='market_daily', request={},
                source_profile=profile_for('daily', identity_map={}), observed_at='2026-10-01T00:00:00Z')
            plain = data.dictionary(domains=('market_daily',))
            self.assertFalse(any(r.get('raw_field') == 'vendor_extra' for r in plain['fields']))
            result = data.dictionary(domains=('market_daily',), raw_batch_id=raw['batch_id'])
            extra = next(r for r in result['fields'] if r.get('raw_field') == 'vendor_extra')
            self.assertFalse(extra['supported'])
            self.assertIsNone(extra['canonical_field'])
            self.assertIsNone(extra['unit'])
