"""Batch source-scope and bounded offline reconstruction semantics."""
import json
import tempfile
import unittest
from unittest.mock import patch

from axiom_data import Data, IngestBatch, UpdateRequest
from axiom_data.provider_local import CONTRACTS, profile_for
from axiom_data.sources import normalize_batch
from axiom_data.protocols import DataError
from axiom_data.storage import LocalStore
from axiom_data.verification import audit_snapshot


class DeliveryProcessingTests(unittest.TestCase):
    def batch(self, rows, selected=('000001.SZ',), endpoint='daily'):
        profile = profile_for(endpoint, identity_map={'000001.SZ': 'one'})
        return IngestBatch(CONTRACTS[endpoint]['contract_id'].split('.')[1], json.dumps(rows).encode(),
            {'endpoint': endpoint, 'params': {'trade_date': '20260102'},
             'request_strategy': 'trading_day_market_v2', 'canonical_symbols': list(selected)},
            CONTRACTS[endpoint], profile, '2026-09-28T01:00:00Z', profile.get('normalizer', 'records_v1'))

    def test_batch_retains_full_raw_but_selects_bound_scope_and_checks_all_dates(self):
        rows = [dict(ts_code=code, trade_date='20260102', open=10, high=11, low=9,
                     close=10, pre_close=9, vol=12.34, amount=45.6)
                for code in ('000001.SZ', '600000.SH')]
        batch = self.batch(rows)
        with tempfile.TemporaryDirectory() as root:
            data = Data(root)
            result = data.update(base_snapshot=None, request=UpdateRequest((batch,), 'first', {}))
            domain = data.store.load_snapshot(result.snapshot_id)['domains']['market_daily']
            actual = data.store.read_partition(domain['partitions'][0]).to_pylist()
            self.assertEqual(len(actual), 1)
            self.assertEqual(actual[0]['volume_shares'], 1234)
            self.assertEqual(json.loads(data.store.read_raw(domain['raw_batch_ids'][0])), rows)
            audit = audit_snapshot(data.store, snapshot_id=result.snapshot_id)
            self.assertEqual(audit['source_mapping_checks']['market_daily'], 1)
            self.assertEqual(audit['status'], 'limited')  # no invented calendar coverage
        rows[1]['trade_date'] = '20260103'
        with self.assertRaisesRegex(DataError, 'outside requested dates'):
            normalize_batch(self.batch(rows), {'batch_id': 'raw'})
        with self.assertRaisesRegex(DataError, 'explicitly bound'):
            normalize_batch(self.batch(rows, selected=('600000.SH',)), {'batch_id': 'raw'})

    def test_audit_keeps_same_day_exchange_calendars_with_null_security_ids(self):
        identities = {'600000.SH': 'sh', '000001.SZ': 'sz'}
        observed = '2026-09-28T01:00:00Z'

        def batch(endpoint, rows, params):
            contract = CONTRACTS[endpoint]
            profile = profile_for(endpoint, identity_map=identities)
            request = {'endpoint': endpoint, 'params': params,
                       'request_strategy': 'trading_day_market_v2'}
            if 'identity_map' in profile:
                request['canonical_symbols'] = list(identities)
            return IngestBatch(contract['contract_id'].split('.')[1],
                json.dumps(rows).encode(), request, contract, profile, observed,
                profile.get('normalizer', 'records_v1'))

        batches = []
        for exchange, code, opened in [('SSE', '600000.SH', ('1', '0')),
                                        ('SZSE', '000001.SZ', ('0', '1'))]:
            batches.append(batch('stock_basic', [dict(ts_code=code, exchange=exchange,
                list_status='L', list_date='20000101', delist_date=None)],
                {'exchange': exchange, 'list_status': 'L'}))
            batches.append(batch('trade_cal', [dict(exchange=exchange, cal_date=day,
                is_open=state) for day, state in zip(('20260102', '20260105'), opened)],
                {'exchange': exchange, 'start_date': '20260102', 'end_date': '20260105'}))
        batches.append(batch('suspend_d', [dict(ts_code='600000.SH',
            trade_date='20260102', suspend_type='S', suspend_timing=None)],
            {'trade_date': '20260102'}))
        batches.append(batch('daily', [dict(ts_code='000001.SZ', trade_date='20260105',
            open=10, high=11, low=9, close=10, pre_close=9, vol=1, amount=1)],
            {'trade_date': '20260105'}))
        with tempfile.TemporaryDirectory() as root:
            data = Data(root)
            result = data.update(base_snapshot=None,
                request=UpdateRequest(tuple(batches), 'two-exchange-calendar', {}))
            calendar = data.store.load_snapshot(result.snapshot_id)['domains']['trading_calendar']
            rows = data.store.read_partition(calendar['partitions'][0]).to_pylist()
            self.assertEqual(len(rows), 4)
            self.assertTrue(all('security_id' in row and row['security_id'] is None for row in rows))
            report = audit_snapshot(data.store, snapshot_id=result.snapshot_id)
            self.assertEqual(report['missing_market_cells'], 1)
            self.assertEqual(report['known_suspension_missing_cells'], 1)
            self.assertEqual(report['missing_market_samples'], [dict(security_id='sh',
                session='2026-01-02', reason='explicit_suspension')])
            self.assertEqual(report['source_mapping_checks']['market_daily'], 1)

    def test_batch_stock_basic_is_exchange_scoped_even_for_unselected_codes(self):
        profile = profile_for('stock_basic', identity_map={'000001.SZ': 'one'})
        row = dict(ts_code='T600018.SH', exchange='SSE', list_status='D',list_date='20000719',delist_date='20061020')
        batch = IngestBatch('security_master',json.dumps([row]).encode(),
            {'endpoint':'stock_basic','params':{'exchange':'SSE','list_status':'D'},
             'canonical_symbols':['000001.SZ']},CONTRACTS['stock_basic'],profile,'2026-09-28T01:00:00Z')
        self.assertEqual(normalize_batch(batch,{'batch_id':'raw'}),[])
        row['exchange']='SZSE'
        batch = IngestBatch(batch.domain,json.dumps([row]).encode(),batch.request,batch.contract,profile,batch.observed_at)
        with self.assertRaisesRegex(DataError,'outside requested exchange'):
            normalize_batch(batch,{'batch_id':'raw'})

    def test_rebuild_reads_each_payload_once_not_all_during_metadata_validation(self):
        with tempfile.TemporaryDirectory() as root:
            data = Data(root)
            row = dict(ts_code='000001.SZ',trade_date='20260102',open=10,high=11,low=9,close=10,pre_close=9,vol=1,amount=1)
            result=data.update(base_snapshot=None,request=UpdateRequest((self.batch([row]),),'ingest',{}))
            domain=data.store.load_snapshot(result.snapshot_id)['domains']['market_daily']
            with patch('axiom_data.updates._NORMALIZED_BUFFER_ROWS', 1), patch.object(data.store,'read_raw_record',wraps=data.store.read_raw_record) as read:
                rebuilt=data.rebuild(base_snapshot=result.snapshot_id,raw_batch_ids=domain['raw_batch_ids'],
                    domains=['market_daily'],operation_id='rebuild',build_context={},promote=False)
                self.assertEqual(read.call_count,1)
            self.assertEqual(data.store.resolve("current"),result.snapshot_id)
            self.assertEqual(data.store.load_snapshot(rebuilt.snapshot_id)['domains']['market_daily']['partitions'],domain['partitions'])

    def test_multiple_daily_suspension_events_are_not_competing_revisions(self):
        profile = profile_for('suspend_d', identity_map={'000001.SZ': 'one'})
        events = [dict(ts_code='000001.SZ', trade_date='20260102', suspend_type='R', suspend_timing=None),
                  dict(ts_code='000001.SZ', trade_date='20260102', suspend_type='S', suspend_timing='09:30-09:30')]
        def normalized(source):
            batch = IngestBatch('security_status', json.dumps(source).encode(),
                {'endpoint': 'suspend_d', 'params': {'trade_date': '20260102'},
                 'canonical_symbols': ['000001.SZ']}, CONTRACTS['suspend_d'], profile,
                '2026-09-28T01:00:00Z', 'tushare_suspend_d_v1')
            return normalize_batch(batch, {'batch_id': 'raw'})
        rows = normalized(events)
        self.assertEqual(rows, normalized(list(reversed(events))))
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['is_suspended'])
        self.assertEqual(rows[0]['status_reason'], 'partial_session_suspension')
        events[1]['suspend_timing'] = None
        self.assertEqual(normalized(events)[0]['status_reason'], 'conflicting_daily_status_events')

    def test_new_member_cannot_be_silently_filtered_from_frozen_historical_union(self):
        profile = profile_for('index_weight', identity_map={'000001.SZ': 'one'})
        batch = IngestBatch('universe_membership', json.dumps([dict(index_code='000300.SH',
            con_code='600000.SH', trade_date='20260102', weight=1)]).encode(),
            {'endpoint': 'index_weight', 'params': {'index_code': '000300.SH', 'trade_date': '20260102'},
             'canonical_symbols': ['000001.SZ']}, CONTRACTS['index_weight'], profile,
            '2026-09-28T01:00:00Z', 'tushare_index_weight_v1')
        with self.assertRaisesRegex(DataError, 'membership source grew'):
            normalize_batch(batch, {'batch_id': 'raw'})
