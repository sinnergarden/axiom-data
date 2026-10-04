"""Whole-action ambiguity from an exact retained dividend response.

Raw b_d3d8089cc98640cabff2720ef7db2cbe, 86 rows, SHA256
2c6adfe23c5a8546d77bbfec22b5c0e5b14dd0912cc0ef8ae95958b7fda0ee47,
receipt 2026-10-04T06:34:34.827527Z. Fixture contains its three 000409.SZ
rows, not the whole payload. It establishes neither action identity nor
revision order between the two implemented rows.
"""
from copy import deepcopy
from datetime import datetime, timezone
from itertools import permutations
import json
from pathlib import Path
import tempfile
import unittest

from axiom_data.event_reader import read_events
from axiom_data.event_sources import CONTRACTS, _dividend_unique, collect_event_response
from axiom_data.portable import export_bundle, import_bundle
from axiom_data.protocols import DataError, EventQuery
from axiom_data.sources import _rows
from axiom_data.storage import LocalStore
from axiom_data.updates import apply_saved_raw
from axiom_data.verification import audit_snapshot

ROWS = json.loads((Path(__file__).parent/'fixtures/dividend_same_observation.json').read_text())
OBSERVED = datetime(2026, 10, 4, 6, 34, 34, 827527, tzinfo=timezone.utc)
IDENTITY = {'000409.SZ': 'sec-conflict', '000001.SZ': 'sec-other'}
CALENDAR = {'2015-05-13':'2015-05-14', '2015-07-16':'2015-07-17', '2018-07-31':'2018-08-01'}


class Client:
    def __init__(self, rows):
        self.rows = rows
    def query(self, *args, **kwargs):
        return self.rows


def publish(store, rows, op='publish', base=None, observed=OBSERVED, calendar=CALENDAR):
    raw = collect_event_response(store, client=Client(rows), endpoint='dividend',
        params={'ann_date':'20150513'}, identity_map=IDENTITY, operation_id=op+'.fetch',
        observed_at=observed, next_open_session_by_date=calendar)
    receipt = apply_saved_raw(store, base_snapshot=base, raw_batch_ids=[raw['batch_id']],
        operation_id=op, build_context={'test':'whole action'}, promote=False)
    return raw, receipt.snapshot_id


def query(store, snapshot, *, day='2015-07-21', symbols=('sec-conflict',),
          cutoff=OBSERVED.isoformat(), policy='operational_pit_v1', filters=None,
          fields=('cash_dividend_before_tax_per_share','bonus_shares_per_share','source_issue')):
    return read_events(store, snapshot, EventQuery('corporate_actions',
        fields,
        symbols, day, day, cutoff, policy, 'ex_date', filters=filters or {'process_status':'实施'}))


class DividendConflictTests(unittest.TestCase):
    def test_real_group_is_order_independent_and_entire_action_is_unavailable(self):
        for rows in permutations(ROWS):
            actions = _dividend_unique(list(rows))
            implemented = next(row for row in actions if row['div_proc']=='实施')
            self.assertEqual(implemented['__source_candidate_count'], 2)
            self.assertEqual(implemented['__source_issue'], 'ambiguous_action_identity_or_revision')
            for field in ('cash_div_tax','stk_bo_rate','stk_co_rate','imp_ann_date','record_date','ex_date'):
                self.assertIsNone(implemented[field])
                self.assertEqual(implemented['__status__'+field], 'source_missing')
            rejected = next(row for row in actions if row['div_proc']=='未通过')
            self.assertEqual(rejected['stk_co_rate'], 0.09)
            self.assertIsNone(rejected['__source_issue'])

    def test_exact_duplicates_are_one_native_action(self):
        result = _dividend_unique([ROWS[0], deepcopy(ROWS[0])])[0]
        self.assertEqual(result['stk_bo_rate'], 0.22275)
        self.assertEqual(result['__source_candidate_count'], 1)
        self.assertIsNone(result['__source_issue'])

    def test_ambiguity_does_not_hide_invalid_source_types(self):
        for field,value in (('imp_ann_date','bad date'),('stk_bo_rate','bad number')):
            with self.assertRaises(DataError):
                _dividend_unique([ROWS[0],{**ROWS[1],field:value}])

    def test_ex_date_query_returns_missing_marker_and_scope_not_silent_absence(self):
        other = {**ROWS[0], 'ts_code':'000001.SZ','end_date':'20141231',
                 'cash_div_tax':0.5,'stk_bo_rate':0.0,'stk_co_rate':0.0}
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root)
            raw, snapshot=publish(store,[*ROWS,other])
            self.assertEqual(audit_snapshot(store,snapshot_id=snapshot)['source_mapping_checks']['corporate_actions'],3)
            for day in ('2015-07-21','2018-08-03'):
                result=query(store,snapshot,day=day)
                self.assertEqual(len(result.frame),1)
                self.assertTrue(result.frame['ex_date'].isna().all())
                self.assertTrue(result.frame['cash_dividend_before_tax_per_share'].isna().all())
                meta=result.field_meta['cash_dividend_before_tax_per_share']['by_key'][0]
                self.assertEqual(meta['status'],'source_missing')
                self.assertEqual(meta['missing_reason'],'ambiguous_action_identity_or_revision')
                self.assertEqual(result.context['unavailable_event_scope'][0]['candidate_dates'],
                                 [day])
                self.assertTrue(any('whole corporate actions' in text for text in result.context['limitations']))
            self.assertEqual(len(query(store,snapshot,day='2016-01-01').frame),0)
            normal=query(store,snapshot,symbols=('sec-other',))
            self.assertEqual(normal.frame['cash_dividend_before_tax_per_share'].iloc[0],0.5)
            self.assertNotIn('unavailable_event_scope',normal.context)
            # A filter on the unanimous zero must not turn the action into an
            # empty result that the account could treat as normal.
            filtered=query(store,snapshot,filters={'process_status':'实施','cash_dividend_before_tax_per_share':0.0})
            self.assertEqual(len(filtered.frame),1)
            self.assertEqual(_rows(store.read_raw_record(raw)),[*ROWS,other])

    def test_unknown_candidate_date_keeps_requested_range_uncertain(self):
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root)
            _, snapshot=publish(store,[ROWS[0],{**ROWS[1],'ex_date':None}])
            result=query(store,snapshot,day='2016-01-01')
            self.assertEqual(len(result.frame),1)
            self.assertIn(None,result.context['unavailable_event_scope'][0]['candidate_dates'])

    def test_changed_candidate_dates_are_a_new_terminal_observation_even_when_count_is_equal(self):
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root)
            _, first=publish(store,ROWS,op='first-ambiguous')
            changed=[{**row,'ex_date':'20150921'} if row['div_proc']=='实施' and row['ex_date']=='20150721'
                     else row for row in ROWS]
            later=datetime(2026,10,4,7,tzinfo=timezone.utc)
            _, second=publish(store,changed,op='changed-candidates',base=first,observed=later)
            self.assertNotEqual(first,second)
            self.assertEqual(len(query(store,second,day='2015-09-21',cutoff=later.isoformat()).frame),1)
            self.assertEqual(len(query(store,second,day='2015-09-21',cutoff=OBSERVED.isoformat()).frame),0)
            self.assertEqual(len(query(store,first).frame),1)

    def test_pit_does_not_use_older_value_after_ambiguous_action_or_reveal_future_notice(self):
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root)
            earlier=datetime(2026,10,4,6,30,tzinfo=timezone.utc)
            _, before=publish(store,[ROWS[0]],op='earlier',observed=earlier)
            _, after=publish(store,ROWS,op='ambiguous',base=before)
            old=query(store,after,cutoff='2026-10-04T06:31:00Z')
            self.assertEqual(old.frame['bonus_shares_per_share'].iloc[0],0.22275)
            self.assertTrue(query(store,after).frame['bonus_shares_per_share'].isna().all())
            historic=query(store,after,policy='best_effort_vendor_v1',cutoff='2015-08-01T12:00:00Z')
            self.assertEqual(len(historic.frame),1)
            self.assertTrue(historic.frame['bonus_shares_per_share'].isna().all())
            self.assertEqual(historic.context['unavailable_event_scope'][0]['candidate_dates'],['2015-07-21'])

    def test_best_effort_early_scope_exists_without_an_earlier_single_observation(self):
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root)
            _, snapshot=publish(store,ROWS)
            for cutoff, expected in (('2015-07-17T01:29:59Z',0),
                                     ('2015-07-17T01:30:00Z',1),
                                     ('2015-08-01T12:00:00Z',1)):
                result=query(store,snapshot,policy='best_effort_vendor_v1',cutoff=cutoff)
                self.assertEqual(len(result.frame),expected)
                if expected:
                    self.assertTrue(result.frame['ex_date'].isna().all())
                    self.assertTrue(result.frame['cash_dividend_before_tax_per_share'].isna().all())
                    self.assertTrue(result.frame['bonus_shares_per_share'].isna().all())
                    self.assertEqual(result.context['unavailable_event_scope'][0]['candidate_dates'],['2015-07-21'])
                    meta=result.field_meta['bonus_shares_per_share']['by_key'][0]
                    self.assertEqual(meta['status'],'source_missing')
                    self.assertEqual(meta['availability_basis'],'declared_vendor_assumption')

    def test_early_scope_does_not_leak_future_candidates_or_require_their_calendar(self):
        fields=('bonus_shares_per_share','source_candidate_count','candidate_economic_dates')
        early_calendar={day:session for day,session in CALENDAR.items() if day<'2016-01-01'}
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root)
            _, snapshot=publish(store,ROWS,calendar=early_calendar)
            result=query(store,snapshot,policy='best_effort_vendor_v1',
                         cutoff='2015-08-01T12:00:00Z',fields=fields)
            self.assertEqual(len(result.frame),1)
            for field in fields:
                self.assertTrue(result.frame[field].isna().all())
                self.assertEqual(result.field_meta[field]['by_key'][0]['status'],'source_missing')
            self.assertEqual(result.context['unavailable_event_scope'][0]['candidate_dates'],['2015-07-21'])
            self.assertNotIn('2018',json.dumps(result.context['unavailable_event_scope']))
            future=query(store,snapshot,day='2018-08-03',policy='best_effort_vendor_v1',
                         cutoff='2015-08-01T12:00:00Z',fields=fields)
            self.assertEqual(len(future.frame),0)
            self.assertNotIn('unavailable_event_scope',future.context)
            # A future candidate with an unknown date also cannot expand the
            # range that is visible before its implementation notice.
            _, unknown=publish(store,[ROWS[0],{**ROWS[1],'ex_date':None}],op='future-unknown',
                               calendar=early_calendar)
            outside=query(store,unknown,day='2016-01-01',policy='best_effort_vendor_v1',
                          cutoff='2015-08-01T12:00:00Z')
            self.assertEqual(len(outside.frame),0)
            self.assertNotIn('unavailable_event_scope',outside.context)

    def test_late_best_effort_cutoff_reports_each_range_without_available_amounts(self):
        fields=('cash_dividend_before_tax_per_share','source_candidate_count','candidate_economic_dates')
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root)
            _, snapshot=publish(store,ROWS)
            for day in ('2015-07-21','2018-08-03'):
                result=query(store,snapshot,day=day,policy='best_effort_vendor_v1',
                             cutoff='2019-01-01T12:00:00Z',fields=fields)
                self.assertEqual(len(result.frame),1)
                self.assertTrue(result.frame['cash_dividend_before_tax_per_share'].isna().all())
                self.assertEqual(result.context['unavailable_event_scope'][0]['candidate_dates'],[day])
                self.assertEqual(result.frame['source_candidate_count'].iloc[0],2)
                summary=json.loads(result.frame['candidate_economic_dates'].iloc[0])
                self.assertEqual(summary['ex_date'],['20150721','20180803'])
                expected=('2015-07-17T09:30:00+08:00' if day=='2015-07-21'
                          else '2018-08-01T09:30:00+08:00')
                self.assertEqual(result.field_meta['cash_dividend_before_tax_per_share']
                                 ['by_key'][0]['usable_from'],expected)

    def test_strict_policies_do_not_backfill_scope_before_actual_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root)
            _, snapshot=publish(store,ROWS)
            for policy in ('operational_pit_v1','market_pit_safe_v1'):
                for cutoff in ('2015-08-01T12:00:00Z','2026-10-04T06:34:34.827526Z'):
                    result=query(store,snapshot,policy=policy,cutoff=cutoff)
                    self.assertEqual(len(result.frame),0)
                    self.assertNotIn('unavailable_event_scope',result.context)
                self.assertEqual(len(query(store,snapshot,policy=policy).frame),1)

    def test_independent_audit_rejects_unanimous_zero_as_an_available_action(self):
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root)
            _, snapshot=publish(store,ROWS)
            domains=store.load_snapshot(snapshot)['domains']
            domain=domains['corporate_actions']
            part=domain['partitions'][0]
            rows=store.read_partition(part).to_pylist()
            row=next(row for row in rows if row['process_status']=='实施')
            row['cash_dividend_before_tax_per_share']=0.0
            row['cash_dividend_before_tax_per_share__status']=None
            domain['partitions']=[store.write_partition('corporate_actions',part['partition'],rows,domain['contract'])]
            altered=store.publish_snapshot(domains,parent_snapshot=snapshot,build_context={'test':'false zero'},promote=False)
            with self.assertRaises(DataError):
                audit_snapshot(store,snapshot_id=altered['snapshot_id'])

    def test_relocated_reader_keeps_raw_scope_for_missing_date(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)
            store=LocalStore(directory/'source')
            _, snapshot=publish(store,ROWS)
            export_bundle(store.root,directory/'bundle',snapshot_id=snapshot,
                          code_root=Path(__file__).resolve().parents[1])
            import_bundle(directory/'bundle',directory/'moved')
            result=query(LocalStore(directory/'moved'),snapshot)
            self.assertEqual(len(result.frame),1)
            self.assertTrue(result.frame['cash_dividend_before_tax_per_share'].isna().all())
            self.assertEqual(audit_snapshot(LocalStore(directory/'moved'),snapshot_id=snapshot)
                             ['source_mapping_checks']['corporate_actions'],2)
