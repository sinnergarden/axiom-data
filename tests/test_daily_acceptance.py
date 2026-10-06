"""Offline daily delivery failures, using only small temporary source roots."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from axiom_data import Data, IngestBatch, UpdateRequest
from axiom_data import cli
from axiom_data.batch_fetch import run_batch_chunk, verify_batch_selectors
from axiom_data.bulk_jobs import plan_bulk_job
from axiom_data.event_sources import CONTRACTS as EVENT_CONTRACTS, event_source_profile
from axiom_data.protocols import DataError
from axiom_data.provider_local import CONTRACTS, FIELDS, profile_for
from axiom_data.updates import rebuild_from_raw
from axiom_data.verification import audit_snapshot
from test_batch_jobs_v2 import WholeMarket, IDS


IDENTITIES = {'000001.SZ': 'one', '000002.SZ': 'two', '600000.SH': 'three'}
DAYS = ('2026-01-02', '2026-01-05')


def source_batch(endpoint, rows, params, *, selected=None, observed='2026-09-28T01:00:00Z'):
    event = endpoint == 'stk_limit'
    contract = (EVENT_CONTRACTS if event else CONTRACTS)[endpoint]
    profile = (event_source_profile if event else profile_for)(endpoint, identity_map=IDENTITIES)
    request = {'endpoint': endpoint, 'params': params,
               'canonical_symbols': sorted(IDENTITIES if selected is None else selected),
               'request_strategy': 'event_bulk_v1' if event else 'trading_day_market_v2'}
    if endpoint == 'trade_cal':
        request.pop('canonical_symbols')
    return IngestBatch(contract['contract_id'].split('.')[1], json.dumps(rows).encode(),
                       request, contract, profile, observed,
                       'event_records_v1' if event else profile.get('normalizer', 'records_v1'))


def facts(endpoint, codes, day, close=10):
    common = [dict(ts_code=code, trade_date=day.replace('-', '')) for code in codes]
    if endpoint == 'daily':
        return [dict(row, open=10, high=11, low=9, close=close, pre_close=9, vol=1, amount=1)
                for row in common]
    if endpoint == 'adj_factor':
        return [dict(row, adj_factor=1.) for row in common]
    return [dict(row, up_limit=11., down_limit=9.) for row in common]


def initialize(data):
    batches = []
    for exchange, codes in [('SZSE', ['000001.SZ', '000002.SZ']), ('SSE', ['600000.SH'])]:
        batches.append(source_batch('stock_basic', [dict(ts_code=code, exchange=exchange,
            list_status='L', list_date='20000101', delist_date=None) for code in codes],
            {'exchange': exchange, 'list_status': 'L'}))
        batches.append(source_batch('trade_cal', [dict(exchange=exchange,
            cal_date=day.replace('-', ''), is_open='1') for day in DAYS],
            {'exchange': exchange, 'start_date': '20260102', 'end_date': '20260105'}))
    for day in DAYS:
        for endpoint in ('daily', 'adj_factor', 'stk_limit'):
            batches.append(source_batch(endpoint, facts(endpoint, IDENTITIES, day),
                                        {'trade_date': day.replace('-', '')}))
    return data.update(base_snapshot=None, request=UpdateRequest(tuple(batches), 'initial', {})).snapshot_id


def plan(codes=None, day=DAYS[1]):
    return plan_bulk_job(mode='daily', symbols=sorted(IDENTITIES if codes is None else codes),
        identity_map=IDENTITIES, start_session=day, end_session=day,
        endpoints=['trade_cal', 'daily', 'adj_factor'])


def altered_snapshot(data, snapshot, domain_name, transform):
    domains = deepcopy(data.store.load_snapshot(snapshot)['domains'])
    domain = domains[domain_name]
    domain['partitions'] = [data.store.write_partition(domain_name, part['partition'],
        transform(data.store.read_partition(part).to_pylist()), domain['contract'])
        for part in domain['partitions']]
    return data.store.publish_snapshot(domains, parent_snapshot=snapshot,
        build_context={'test': 'independent acceptance corruption'}, promote=False)['snapshot_id']


class DailyAcceptanceTests(unittest.TestCase):
    def test_partial_repeat_real_revision_empty_and_other_date_preserve_base(self):
        with tempfile.TemporaryDirectory() as root:
            data = Data(root)
            initial = initialize(data)
            previous = initial
            for index, (rows, day) in enumerate([
                (facts('daily', ['000001.SZ'], DAYS[1]), DAYS[1]),
                (facts('daily', ['000001.SZ'], DAYS[1], close=12), DAYS[1]),
                ([], DAYS[1]),
                (facts('daily', ['000001.SZ'], '2026-01-06', close=13), '2026-01-06'),
            ]):
                batch = source_batch('daily', rows, {'trade_date': day.replace('-', '')},
                    selected=['000001.SZ'], observed=f'2026-09-29T0{index}:00:00Z')
                current = data.update(base_snapshot=previous,
                    request=UpdateRequest((batch,), f'partial-{index}', {})).snapshot_id
                if index == 0 or index == 2:
                    self.assertEqual(current, previous)
                report = audit_snapshot(data.store, snapshot_id=current, plan=plan(['000001.SZ'], day=day),
                                        base_snapshot=previous, preserve_base=True)
                self.assertGreater(report['preserved_base_rows'], 0)
                previous = current
            domain = data.store.load_snapshot(previous)['domains']['market_daily']
            rows = data.store.read_partition(domain['partitions'][0]).to_pylist()
            versions = [row for row in rows if row['security_id'] == 'one' and str(row['session']) == DAYS[1]]
            self.assertEqual([row['close'] for row in versions], [10., 12.])
            initial_rows = data.store.read_partition(data.store.load_snapshot(initial)['domains']
                                                   ['market_daily']['partitions'][0]).to_pylist()
            self.assertTrue(all(row in rows for row in initial_rows))

    def test_raw_present_fact_cannot_disappear_even_without_base(self):
        for name in ('market_daily', 'adjustment_factors', 'price_limits'):
            with self.subTest(domain=name), tempfile.TemporaryDirectory() as root:
                data = Data(root)
                snapshot = initialize(data)
                altered = altered_snapshot(data, snapshot, name, lambda rows: [row for row in rows
                    if not (row['security_id'] == 'two' and str(row['session']) == DAYS[1])])
                with self.assertRaisesRegex(DataError, 'selected Raw fact absent'):
                    audit_snapshot(data.store, snapshot_id=altered, plan=plan())
                # An entire missing month is also detected from retained Raw.
                altered = altered_snapshot(data, snapshot, name, lambda rows: [])
                with self.assertRaisesRegex(DataError, 'selected Raw fact absent'):
                    audit_snapshot(data.store, snapshot_id=altered, plan=plan())

    def test_incremental_guard_catches_loss_outside_representative_scope(self):
        with tempfile.TemporaryDirectory() as root:
            data = Data(root)
            snapshot = initialize(data)
            altered = altered_snapshot(data, snapshot, 'market_daily', lambda rows:
                [row for row in rows if row['security_id'] != 'three'])
            with self.assertRaisesRegex(DataError, 'old revision/value/receipt'):
                audit_snapshot(data.store, snapshot_id=altered, plan=plan(['000001.SZ']),
                               base_snapshot=snapshot, preserve_base=True)

    def test_incremental_guard_binds_original_receipt_and_revision_metadata(self):
        for field, wrong in [('raw_batch_id', 'wrong-receipt'),
                             ('first_observed_at', '2026-09-29T01:00:00Z'),
                             ('revision_id', 'wrong-revision')]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                data = Data(root)
                snapshot = initialize(data)
                def corrupt(rows):
                    rows[0][field] = wrong
                    return rows
                altered = altered_snapshot(data, snapshot, 'market_daily', corrupt)
                with self.assertRaisesRegex(DataError, 'old revision/value/receipt'):
                    audit_snapshot(data.store, snapshot_id=altered, plan=plan(['000001.SZ']),
                                   base_snapshot=snapshot, preserve_base=True)

    def test_field_gaps_use_latest_revision_and_remain_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            data = Data(root)
            snapshot = initialize(data)
            batches = []
            for endpoint, field in [('daily', 'open'), ('adj_factor', 'adj_factor'), ('stk_limit', 'up_limit')]:
                rows = facts(endpoint, ['000001.SZ'], DAYS[1])
                rows[0][field] = None
                batches.append(source_batch(endpoint, rows, {'trade_date': '20260105'},
                    selected=['000001.SZ'], observed='2026-09-29T01:00:00Z'))
            current = data.update(base_snapshot=snapshot,
                request=UpdateRequest(tuple(batches), 'nullable-fields', {})).snapshot_id
            report = audit_snapshot(data.store, snapshot_id=current, plan=plan(),
                                    base_snapshot=snapshot, preserve_base=True)
            self.assertEqual(report['status'], 'limited')
            for domain, field in [('market_daily', 'open'), ('adjustment_factors', 'factor'), ('price_limits', 'up_limit')]:
                self.assertEqual(report['daily_field_coverage'][domain]['expected_keys'], 3)
                self.assertEqual(report['daily_field_coverage'][domain]['missing_by_field'][field], 1)

    def test_explicit_selected_raw_rebuild_still_replaces_full_domain(self):
        with tempfile.TemporaryDirectory() as root:
            data = Data(root)
            original = initialize(data)
            batch = source_batch('daily', facts('daily', ['000001.SZ'], DAYS[1], close=12),
                {'trade_date': '20260105'}, selected=['000001.SZ'], observed='2026-09-29T01:00:00Z')
            revised = data.update(base_snapshot=original,
                request=UpdateRequest((batch,), 'revision', {})).snapshot_id
            domain = data.store.load_snapshot(original)['domains']['market_daily']
            rebuilt = rebuild_from_raw(data.store, base_snapshot=revised,
                raw_batch_ids=domain['raw_batch_ids'], domains=['market_daily'],
                operation_id='selected-raw-rebuild', build_context={}, promote=False).snapshot_id
            audit_snapshot(data.store, snapshot_id=rebuilt, plan=plan(), base_snapshot=revised)
            with self.assertRaisesRegex(DataError, 'old revision/value/receipt'):
                audit_snapshot(data.store, snapshot_id=rebuilt, base_snapshot=revised, preserve_base=True)

    def test_plan_binds_full_scope_and_keeps_original_unbound_plans_readable(self):
        with tempfile.TemporaryDirectory() as root:
            scope = {'mode': 'daily', 'symbols': [f'{n:06d}.SZ' for n in range(3497)],
                     'identity_map': {f'{n:06d}.SZ': f'sec-{n}' for n in range(3497)},
                     'start_session': DAYS[1], 'end_session': DAYS[1], 'endpoints': ['trade_cal', 'daily']}
            body = {'schema_version': cli._PLAN_SCHEMA_V2, 'job': plan_bulk_job(**scope).to_dict(),
                    'operation_id': 'full-pool', 'base_snapshot': None, 'promote': False,
                    'max_attempts': 1, 'min_interval_seconds': 0., 'max_workers': 1,
                    'global_calls_per_minute': 300, 'stock_basic_calls_per_minute': 50,
                    'source_scope': scope, 'source_scope_sha256': cli._digest(scope)}
            path = Path(root) / 'plan.json'
            def save():
                path.write_text(json.dumps(dict(body, plan_sha256=cli._digest(body))))
            save()
            _, job = cli._load_plan(path)
            self.assertEqual(len(job.symbols), 3497)
            body['job']['symbols'] = body['job']['symbols'][:16]
            save()  # Both the outer digest and the scope digest are valid.
            with self.assertRaisesRegex(ValueError, 'frozen source scope'):
                cli._load_plan(path)
            body.pop('source_scope')
            body.pop('source_scope_sha256')
            save()
            self.assertEqual(len(cli._load_plan(path)[1].symbols), 16)

    def test_correct_split_union_passes_and_omitted_child_fails(self):
        with tempfile.TemporaryDirectory() as root:
            store = Data(root).store
            specs = [{'endpoint': 'daily', 'params': {'trade_date': '20200102'},
                      'fields': list(FIELDS['daily']), 'canonical_symbols': sorted(IDS)}]
            run_batch_chunk(store, specs=specs, client=WholeMarket(cap_daily=True),
                operation_id='split', plan_fingerprint='frozen', identity_map=IDS, raw_log_offset=0,
                global_calls_per_minute=0, stock_basic_calls_per_minute=0)
            def verify():
                return verify_batch_selectors(store, operation_id='split', specs=specs,
                                              plan_fingerprint='frozen', identity_map=IDS)
            self.assertEqual(len(verify()), 2)
            state = store.read_operation('split')
            state['tasks'][0]['child_indexes'].pop()
            store.write_operation('split', state)
            with self.assertRaisesRegex(DataError, 'complete parent scope'):
                verify()

    def test_date_split_leaves_cover_parent_without_full_scope_per_leaf(self):
        class CappedCalendar:
            def query(self, endpoint, **params):
                first, last = params['start_date'], params['end_date']
                if first == '20200102' and last == '20200103':
                    return [{}] * 1000
                return [dict(exchange='SSE', cal_date=first, is_open='1')]
        with tempfile.TemporaryDirectory() as root:
            store = Data(root).store
            specs = [{'endpoint': 'trade_cal', 'params': {'exchange': 'SSE',
                      'start_date': '20200102', 'end_date': '20200103'},
                      'fields': list(FIELDS['trade_cal'])}]
            run_batch_chunk(store, specs=specs, client=CappedCalendar(), operation_id='dates',
                plan_fingerprint='frozen', identity_map=IDS, raw_log_offset=0,
                global_calls_per_minute=0, stock_basic_calls_per_minute=0)
            self.assertEqual(len(verify_batch_selectors(store, operation_id='dates', specs=specs,
                plan_fingerprint='frozen', identity_map=IDS)), 2)


if __name__ == '__main__':
    unittest.main()
