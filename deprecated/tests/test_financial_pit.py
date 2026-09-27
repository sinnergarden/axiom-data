"""Counterexamples for PR6 revision time, membership and cumulative arithmetic."""
import unittest

from axiom_data.pit import financial_derived, historical_union, members, select_revisions
from axiom_data.domains.market import MarketContractError

POLICY = 'operational_pit_v1'
CUTOFF = '2025-06-01T00:00:00Z'


def fact(period, revenue, observed='2025-01-01T00:00:00Z', revision='original'):
    return {'symbol': '600000.SH', 'endpoint': 'income', 'report_period': period,
            'report_type': '1', 'logical_event_key': 'income:' + period,
            'revision_id': revision + ':' + period, 'values': {
                'revenue': revenue, 'oper_cost': revenue / 2 if revenue is not None else None,
                'net_income': revenue / 10 if revenue is not None else None},
            'source_ref': 'raw-' + revision, 'first_observed_at': observed,
            'source_available_at': None, 'vendor_available_at': '2024-01-01T00:00:00Z',
            'pit_qualification': 'best_effort'}


def span(start, end, key, industry='bank'):
    row = fact('2024-03-31', 1)
    row.update(group_id='test', effective_from=start, effective_to=end,
               logical_event_key=key, industry_id=industry)
    return row


class FundamentalsPitTest(unittest.TestCase):
    def test_old_vendor_date_cannot_backfill_new_revision(self):
        old = fact('2024-03-31', 100)
        new = fact('2024-03-31', 200, '2025-07-01T00:00:00Z', 'restate')
        for policy in ('operational_pit_v1', 'market_pit_safe_v1'):
            self.assertEqual(select_revisions([old], policy=policy, knowledge_cutoff=CUTOFF),
                             select_revisions([old, new], policy=policy, knowledge_cutoff=CUTOFF))
        selected = select_revisions([old, new], policy=POLICY,
                                    knowledge_cutoff='2025-08-01T00:00:00Z')
        self.assertEqual(selected[0]['values']['revenue'], 200)

    def test_same_date_multiple_periods_and_conflicting_versions(self):
        first = fact('2024-03-31', 100)
        second = fact('2024-06-30', 300)
        self.assertEqual(len(select_revisions([first, second], policy=POLICY,
                                             knowledge_cutoff=CUTOFF)), 2)
        conflict = fact('2024-03-31', 200, revision='conflict')
        with self.assertRaisesRegex(MarketContractError, 'ambiguous'):
            select_revisions([first, conflict], policy=POLICY, knowledge_cutoff=CUTOFF)

    def test_ttm_components_and_max_availability(self):
        rows = [fact(p, v) for p, v in zip(
            ['2024-03-31','2024-06-30','2024-09-30','2024-12-31'], [100,300,600,1000])]
        rows[-1]['first_observed_at'] = '2025-03-01T00:00:00Z'
        derived = financial_derived(rows, policy=POLICY, knowledge_cutoff=CUTOFF)
        ttm = next(r for r in derived if r['field']=='ttm_revenue' and r['report_period']=='2024-12-31')
        self.assertEqual(ttm['value'], 1000)
        self.assertEqual(len(ttm['quarter_components']), 4)
        self.assertEqual(len(ttm['component_revisions']), 4)
        self.assertEqual(ttm['usable_from'], '2025-03-01T00:00:00+00:00')
        early = financial_derived(rows, policy=POLICY, knowledge_cutoff='2025-02-01T00:00:00Z')
        self.assertFalse(any(r['value'] is not None for r in early if r['field']=='ttm_revenue'))

    def test_missing_quarter_nan_and_report_type(self):
        rows = [fact('2024-03-31',100),fact('2024-09-30',600),fact('2024-12-31',1000)]
        derived = financial_derived(rows, policy=POLICY, knowledge_cutoff=CUTOFF)
        self.assertFalse(any(r['value'] is not None for r in derived if r['field']=='ttm_revenue'))
        revised = fact('2024-03-31',None,'2025-03-01T00:00:00Z','null')
        derived = financial_derived([rows[0],revised], policy=POLICY, knowledge_cutoff=CUTOFF)
        self.assertIsNone(next(r for r in derived if r['field']=='single_quarter_revenue')['value'])
        revised['report_type']='2'
        derived = financial_derived([revised], policy=POLICY, knowledge_cutoff=CUTOFF)
        self.assertEqual(derived[0]['missing_reason'], 'incompatible_report_type')

    def test_half_open_entry_exit_reentry_and_historical_union(self):
        rows = [span('2024-01-01','2024-03-01','entry'),span('2024-05-01',None,'reentry')]
        args = dict(knowledge_cutoff=CUTOFF,policy=POLICY,group_id='test')
        self.assertEqual(len(members(rows,target_session='2024-01-01',**args)),1)
        self.assertFalse(members(rows,target_session='2024-03-01',**args))
        self.assertEqual(len(members(rows,target_session='2024-05-01',**args)),1)
        self.assertEqual(historical_union(rows,start_session='2024-03-01',end_session='2024-04-01',
                                          lookback_start='2024-02-01',**args),('600000.SH',))
        conflicting = span('2024-02-01',None,'conflict','insurance')
        with self.assertRaisesRegex(MarketContractError,'overlapping'):
            members(rows+[conflicting],target_session='2024-02-01',**args)

    def test_verified_remains_closed_and_timezones_are_absolute(self):
        row = fact('2024-03-31',1,'2025-06-01T08:00:00+08:00')
        self.assertEqual(len(select_revisions([row],policy=POLICY,knowledge_cutoff=CUTOFF)),1)
        row['pit_qualification']='verified'
        with self.assertRaisesRegex(MarketContractError,'VERIFIED'):
            select_revisions([row],policy=POLICY,knowledge_cutoff=CUTOFF)


if __name__ == '__main__':
    unittest.main()
