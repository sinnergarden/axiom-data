"""Supplier listing dates survive mapping, strict receipts and offline review."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import tempfile
import unittest

from axiom_data.api import Data
from axiom_data.protocols import DataError, QuerySpec
from axiom_data.provider_local import CONTRACTS, profile_for
from axiom_data.storage import LocalStore
from axiom_data.vendor_listing import publish_vendor_listing
from axiom_data.verification import audit_snapshot

OBSERVED = '2026-10-03T00:00:00+00:00'
IDENTITY = {'600001.SH': 'cnstock.600001.SH.19980122'}


def fixture(store):
    ids = []
    for exchange in ('SSE', 'SZSE'):
        for status in ('L', 'D', 'P'):
            rows = ([{'ts_code': '600001.SH', 'exchange': 'SSE', 'list_status': 'D',
                      'list_date': '19980122', 'delist_date': '20091229'}]
                    if (exchange, status) == ('SSE', 'D') else [])
            raw = store.write_raw(json.dumps(rows).encode(), domain='reference_bootstrap',
                request={'endpoint': 'stock_basic', 'params': {'exchange': exchange, 'list_status': status}},
                source_profile={'id': 'tushare.reference_bootstrap.stock_basic.v1'},
                observed_at=OBSERVED, status='success' if rows else 'empty',
                operation_id='listing-fixture', batch_index=len(ids))
            ids.append(raw['batch_id'])
    receipt = publish_vendor_listing(store, stock_basic_raw_batch_ids=ids,
        identity_map=IDENTITY, operation_id='listing-publish', base_snapshot=None, promote=False)
    return receipt['snapshot_id']


class VendorListingAuditTests(unittest.TestCase):
    def test_audit_detects_local_date_and_stable_identity_errors(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            snap = fixture(store)
            report = audit_snapshot(store, snapshot_id=snap)
            self.assertEqual(report['source_mapping_checks']['listing_events'], 2)
            for field, wrong, message in [('delisting_date', '2009-12-30', 'date/boundary'),
                                          ('security_id', 'wrong-security', 'stable identity')]:
                manifest = store.load_snapshot(snap)
                domain = manifest['domains']['listing_events']
                rows = store.read_partition(domain['partitions'][0]).to_pylist()
                target = next(row for row in rows if row['event_type'] == 'delisting')
                target[field] = wrong
                domain['partitions'] = [store.write_partition('listing_events', 'history', rows, domain['contract'])]
                altered = store.publish_snapshot(manifest['domains'], parent_snapshot=snap,
                    build_context={'test': field}, promote=False)['snapshot_id']
                with self.assertRaisesRegex(DataError, message):
                    audit_snapshot(store, snapshot_id=altered)

    def test_supplier_delisting_date_is_exclusive_and_receipt_is_not_backdated(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            snap = fixture(store)
            domains = deepcopy(store.load_snapshot(snap)['domains'])
            days = ('2009-12-28', '2009-12-29', '2009-12-30')
            common = {'revision_id': 'r1', 'revision_sequence': 1,
                      'first_observed_at': OBSERVED, 'source_available_at': None, 'evidence_ref': None}
            master = [{**common, 'security_id': IDENTITY['600001.SH'], 'exchange': 'SSE',
                       'listing_date': '1998-01-22', 'delisting_date': None,
                       'vendor_delist_date': '2009-12-29', 'list_status': 'D'}]
            calendar = [{**common, 'exchange': 'SSE', 'session': day, 'is_open': True} for day in days]
            statuses = [{**common, 'security_id': IDENTITY['600001.SH'], 'session': day,
                         'is_suspended': False, 'status_reason': 'fixture-normal', 'suspend_timing': None}
                        for day in days]
            prices = [{**common, 'security_id': IDENTITY['600001.SH'], 'session': days[0],
                       'open': 10., 'high': 10., 'low': 10., 'close': 10., 'pre_close': 10.,
                       'volume_shares': 100, 'amount_cny': 1000.}]
            for endpoint, rows in [('stock_basic', master), ('trade_cal', calendar),
                                   ('suspend_d', statuses), ('daily', prices)]:
                contract = CONTRACTS[endpoint]
                name = contract['contract_id'].split('.')[1]
                raw = store.write_raw(b'[]', domain=name, request={'endpoint': 'fixture'},
                    source_profile={'id': 'fixture'}, observed_at=OBSERVED)
                for row in rows:
                    row['raw_batch_id'] = raw['batch_id']
                part = store.write_partition(name, 'history', rows, contract)
                domains[name] = {'contract': contract, 'source_profile': profile_for(endpoint, identity_map=IDENTITY),
                                 'partitions': [part], 'raw_batch_ids': [raw['batch_id']], 'coverage': {},
                                 'build_context': {'test': 'supplier-boundary'}}
            snap = store.publish_snapshot(domains, parent_snapshot=snap,
                build_context={'test': 'supplier-boundary'}, promote=False)['snapshot_id']
            data = Data(root)
            def read(policy, cutoff):
                query = QuerySpec(domain='market_daily', fields=('close',), symbols=tuple(IDENTITY.values()),
                    sessions=days, pit_policy=policy, cutoff_by_session={day: cutoff for day in days})
                return data.states(snapshot=snap, query=query).frame.market_state.tolist()
            expected = ['normal_trading', 'delisted', 'delisted']
            self.assertEqual(read('best_effort_vendor_v1', '2010-01-01T00:00:00+08:00'), expected)
            self.assertEqual(read('operational_pit_v1', '2010-01-01T00:00:00+08:00'), ['unknown_status'] * 3)
            self.assertEqual(read('operational_pit_v1', '2026-10-04T00:00:00+08:00'), expected)


if __name__ == '__main__':
    unittest.main()
