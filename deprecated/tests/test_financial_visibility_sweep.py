"""The bounded latest-quarter sweep preserves the existing financial PIT rows."""
import copy
import unittest
from bisect import bisect_right

from axiom_data.pit import (financial_derived_from_selected, instant,
    latest_financial_income_window, select_financial_revisions, visibility_times)
from test_financial_pit import fact


class FinancialVisibilitySweepTest(unittest.TestCase):
    def test_latest_income_window_is_independent_for_three_report_progresses(self):
        periods_by_symbol = {
            '600001.SH': ('2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31'),
            '600002.SH': ('2024-03-31', '2024-06-30', '2024-09-30', '2024-12-31'),
            '600003.SH': ('2023-03-31', '2023-06-30', '2023-09-30', '2023-12-31'),
        }
        rows = []
        for symbol, periods in periods_by_symbol.items():
            older = f'{int(periods[0][:4]) - 2}-12-31'
            for period, revenue in [(older, 5), *zip(periods, (100, 300, 600, 1000))]:
                row = fact(period, revenue, observed='2026-01-01T00:00:00Z')
                row.update(symbol=symbol, logical_event_key=f'income:{symbol}:{period}',
                           revision_id=f'original:{symbol}:{period}', source_ref=f'raw:{symbol}')
                rows.append(row)
        policy = 'operational_pit_v1'
        cutoff = '2026-02-01T00:00:00Z'
        selected = select_financial_revisions(rows, policy=policy, knowledge_cutoff=cutoff)
        full = financial_derived_from_selected(selected, policy=policy, knowledge_cutoff=cutoff)
        window = latest_financial_income_window(selected)
        self.assertEqual(len(window), 12)
        bounded = financial_derived_from_selected(window,
                                                  policy=policy, knowledge_cutoff=cutoff)
        for symbol, periods in periods_by_symbol.items():
            latest = periods[-1]
            reference = tuple(row for row in full if row['symbol'] == symbol
                              and row['report_period'] == latest)
            actual = tuple(row for row in bounded if row['symbol'] == symbol
                           and row['report_period'] == latest)
            self.assertEqual(actual, reference)  # values, missing reasons and provenance
            ttm = next(row for row in actual if row['field'] == 'ttm_revenue')
            self.assertEqual(ttm['value'], 1000)
            self.assertIsNone(ttm['missing_reason'])
            self.assertEqual(len(ttm['component_revisions']), 4)

    def test_latest_leaves_match_full_derivation_across_revision_and_future_gap(self):
        periods = ('2024-03-31','2024-06-30','2024-09-30','2024-12-31',
                   '2025-03-31','2025-06-30','2025-09-30','2025-12-31')
        rows = [fact(period, 100 * (index + 1), observed='2025-01-01T00:00:00Z')
                for index, period in enumerate(periods) if period != '2025-06-30']
        revised = fact('2025-03-31', 550, observed='2025-04-01T00:00:00Z', revision='revised')
        later = fact('2025-06-30', 600, observed='2025-09-01T00:00:00Z')
        conflict = copy.deepcopy(later)
        conflict.update(revision_id='conflict:2025-06-30', source_ref='raw-conflict')
        conflict['values']['revenue'] = 601
        rows.extend((revised, later, conflict))
        policy = 'operational_pit_v1'
        times = visibility_times(rows, policy)
        cached_epoch = -1
        cached_selected = ()
        for cutoff in ('2025-02-01T00:00:00Z','2025-03-01T00:00:00Z',
                       '2025-04-01T00:00:00Z','2025-08-31T00:00:00Z',
                       '2025-09-01T00:00:00Z','2025-10-01T00:00:00Z'):
            epoch = bisect_right(times, instant(cutoff))
            if epoch != cached_epoch:
                cached_selected = select_financial_revisions(rows, policy=policy,
                                                              knowledge_cutoff=cutoff)
                cached_epoch = epoch
            direct = select_financial_revisions(rows, policy=policy, knowledge_cutoff=cutoff)
            self.assertEqual(cached_selected, direct)
            full = financial_derived_from_selected(direct, policy=policy,
                knowledge_cutoff=cutoff)
            bounded = financial_derived_from_selected(latest_financial_income_window(cached_selected),
                policy=policy, knowledge_cutoff=cutoff)
            latest_period = max(r['report_period'] for r in direct if r['endpoint']=='income')
            full_latest = tuple(r for r in full if r['report_period']==latest_period)
            bounded_latest = tuple(r for r in bounded if r['report_period']==latest_period)
            self.assertEqual(bounded_latest,full_latest)
            if cutoff < '2025-09-01T00:00:00Z':
                self.assertFalse(any(r['report_period']=='2025-06-30' for r in direct))
            else:
                self.assertTrue(any(r.get('ambiguous_fields') for r in direct))


if __name__ == '__main__':
    unittest.main()
