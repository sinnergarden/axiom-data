import unittest

from axiom_data.domains.market import MarketContractError
from axiom_data.domains.reference import validate_strict_decision_time


class StrictDecisionTimeTest(unittest.TestCase):
    def row(self, timestamp, **changes):
        return dict(first_observed_at=timestamp, source_available_at=None,
                    availability_basis='first_observation', pit_qualification='observed',
                    source_ref='fixture', **changes)

    def test_market_day_boundary_is_inclusive_and_offset_independent(self):
        for timestamp in ('2025-06-13T15:59:59.999999Z',
                          '2025-06-13T23:59:59.999999+08:00',
                          '2025-06-14T01:59:59.999999+10:00'):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(validate_strict_decision_time([self.row(timestamp)], '2025-06-13'), 'observed')
        for timestamp in ('2025-06-13T16:00:00Z', '2025-06-14T00:00:00+08:00',
                          '2025-06-13T20:00:00Z', '2025-06-14T04:00:00+08:00'):
            with self.subTest(timestamp=timestamp), self.assertRaisesRegex(MarketContractError, 'later than'):
                validate_strict_decision_time([self.row(timestamp)], '2025-06-13')

    def test_unqualified_and_unproven_observations_remain_blocked(self):
        row=self.row('2025-06-13T12:00:00+08:00')
        for changes in ({'pit_qualification':'best_effort', 'availability_basis':'terminal_history_observed'},
                        {'pit_qualification':'unknown'},
                        {'source_available_at':'2025-06-12T12:00:00Z'},
                        {'first_observed_at':'2025-06-13T12:00:00'}):
            with self.subTest(changes=changes), self.assertRaises(MarketContractError):
                validate_strict_decision_time([dict(row, **changes)], '2025-06-13')
