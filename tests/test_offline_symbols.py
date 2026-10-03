"""Whole-market Raw can expand canonical scope without a second supplier call."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from axiom_data import Data, QuerySpec
from axiom_data.protocols import ConflictError, DataError
from axiom_data.provider_local import profile_for
from axiom_data.storage import LocalStore
from test_bulk_jobs import Client, IDS, OBS, job, run


class OfflineSymbolsTests(unittest.TestCase):
    def test_expand_saved_raw_preserves_old_inputs_and_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(directory)
            client = Client()
            before = run(store, job(ids={'000001.SZ': 'sec-1'}), client, 'initial')
            calls = list(client.calls)
            parent = store.load_snapshot(before.snapshot_id)
            old_bytes = (store.root/'snapshots'/f'{before.snapshot_id}.json').read_bytes()
            log = (store.root/'raw/fetches.jsonl').read_bytes()
            raw_ids = parent['domains']['market_daily']['raw_batch_ids']
            raw = {i: deepcopy(store.get_raw(i)) for i in raw_ids}
            request = dict(base_snapshot=before.snapshot_id, raw_batch_ids=raw_ids,
                domains=('market_daily',), operation_id='expand-saved-symbols',
                build_context={'purpose': 'offline expansion'}, promote=False,
                domain_overrides={'market_daily': {
                    'source_profile': profile_for('daily', identity_map=IDS),
                    'canonical_symbols': list(IDS)}})
            result = Data(directory).rebuild(**request)
            self.assertEqual(Data(directory).rebuild(**request), result)
            current = store.load_snapshot(result.snapshot_id)
            self.assertEqual(current['domains']['trading_calendar'], parent['domains']['trading_calendar'])
            self.assertEqual(current['domains']['market_daily']['build_context']['canonical_selection'],
                             {'market_daily': list(IDS)})
            rows = [row for part in current['domains']['market_daily']['partitions']
                    for row in store.read_partition(part).to_pylist()]
            self.assertEqual({r['security_id'] for r in rows}, {'sec-1', 'sec-2'})
            self.assertEqual({r['first_observed_at'] for r in rows}, {OBS})
            self.assertEqual(client.calls, calls)
            self.assertEqual((store.root/'raw/fetches.jsonl').read_bytes(), log)
            self.assertEqual({i: store.get_raw(i) for i in raw_ids}, raw)
            self.assertEqual((store.root/'snapshots'/f'{before.snapshot_id}.json').read_bytes(), old_bytes)
            self.assertEqual(store.resolve('current'), before.snapshot_id)
            query = QuerySpec('market_daily', ('close',), ('sec-1', 'sec-2'), ('2020-01-02',),
                'operational_pit_v1', {'2020-01-02': '2026-09-29T00:00:00Z'})
            self.assertEqual(Data(directory).read(snapshot=result.snapshot_id, query=query).frame['close'].tolist(), [10, 10])
            with self.assertRaises(ConflictError):
                changed = deepcopy(request)
                changed['domain_overrides']['market_daily']['canonical_symbols'] = ['000001.SZ']
                Data(directory).rebuild(**changed)

    def test_no_identity_remap_or_unknown_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(directory)
            first = run(store, job(ids={'000001.SZ': 'sec-1'}), Client(), 'initial')
            ids = store.load_snapshot(first.snapshot_id)['domains']['market_daily']['raw_batch_ids']
            for name, mapping, symbols, error in (
                ('remap', {'000001.SZ': 'changed', '000002.SZ': 'sec-2'}, list(IDS), ConflictError),
                ('unknown', IDS, ['000003.SZ'], DataError),
                ('duplicate', IDS, ['000001.SZ'] * 2, DataError)):
                with self.subTest(name=name), self.assertRaises(error):
                    Data(directory).rebuild(base_snapshot=first.snapshot_id, raw_batch_ids=ids,
                        domains=('market_daily',), operation_id=name, build_context={}, promote=False,
                        domain_overrides={'market_daily': {'canonical_symbols': symbols,
                            'source_profile': profile_for('daily', identity_map=mapping)}})
            self.assertEqual(store.resolve('current'), first.snapshot_id)


if __name__ == '__main__':
    unittest.main()
