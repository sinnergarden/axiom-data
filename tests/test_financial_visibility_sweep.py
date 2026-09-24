"""The bounded latest-quarter sweep preserves the existing financial PIT rows."""
import copy
import unittest
from bisect import bisect_right

from axiom_data.pit import (financial_derived_from_selected, instant,
    latest_financial_income_window, select_financial_revisions, visibility_times)
from test_pr6_pit import fact


class FinancialVisibilitySweepTest(unittest.TestCase):
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
