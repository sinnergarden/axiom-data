"""Numerical and PIT invariants for pure common-anchor price adjustment."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest

import pandas as pd
import pyarrow as pa

from axiom_data.derived import PRICE_ADJUSTMENT_VERSION, adjust_prices
from axiom_data.protocols import DataBatch, QueryError, QuerySpec
from axiom_data.reader import SnapshotQueryReader


EARLY = "2024-01-02"
LATE = "2024-01-03"
CUTOFF = datetime(2024, 1, 3, 10, tzinfo=timezone.utc).isoformat()


def batch(domain, field, values, *, sessions=(EARLY, LATE), snapshot="s1",
          cutoffs=None, purpose="decision_facts", policy="operational_pit_v1"):
    cutoffs = cutoffs or {session: CUTOFF for session in sessions}
    records = [{"security_id": "A", "session": session, field: values.get(session)}
               for session in sessions]
    provenance = [{"security_id": "A", "session": session,
                   "revision_id": f"{domain}-{session}", "revision_sequence": 1,
                   "raw_batch_id": f"raw-{domain}-{session}",
                   "usable_from": "2024-01-03T09:00:00+00:00",
                   "first_observed_at": "2024-01-03T09:00:00+00:00",
                   "availability_basis": "first_observed_at", "evidence_ref": None,
                   "missing_reason": "not_provided" if values.get(session) is None else None}
                  for session in sessions]
    return DataBatch(
        pd.DataFrame.from_records(records),
        {field: {"dtype": "float64", "unit": "CNY" if domain == "market_daily" else None,
                 "by_key": provenance}},
        {"contract_version": "data_batch_v1", "snapshot_id": snapshot,
         "domain": domain, "contract_id": f"{domain}_v1", "source_profile_id": "fixture",
         "reader_version": "local_reader_v1", "query": {
             "fields": [field], "symbols": ["A"], "sessions": list(sessions),
             "pit_policy": policy, "cutoff_by_session": cutoffs,
             "purpose": purpose, "price_basis": "unadjusted", "adjustment_anchor": None,
             "universe_id": None},
         "limitations": ["fixture historical availability assumption"]},
    )


def inputs():
    prices = batch("market_daily", "close", {EARLY: 10.0, LATE: 5.0})
    factors = batch("cumulative_factors", "adj_factor", {EARLY: 1.0, LATE: 2.0})
    return prices, factors


def transformed(prices, factors, **kwargs):
    return adjust_prices(prices, factors, fields=("close",), anchor_session=LATE, **kwargs)


class CommonAnchorAdjustmentTest(unittest.TestCase):
    def test_split_has_zero_adjusted_return_and_per_field_lineage(self):
        prices, factors = inputs()
        result = transformed(prices, factors)
        self.assertEqual(result.frame["close"].tolist(), [5.0, 5.0])
        self.assertEqual(result.frame.loc[1, "close"] / result.frame.loc[0, "close"] - 1, 0.0)
        self.assertEqual(result.field_meta["close"]["unit"], "CNY")
        self.assertEqual(result.field_meta["close"]["recipe_version"], PRICE_ADJUSTMENT_VERSION)
        first = result.field_meta["close"]["by_key"][0]
        self.assertEqual(first["price_provenance"]["revision_id"], "market_daily-2024-01-02")
        self.assertEqual(first["factor_provenance"]["revision_id"], "cumulative_factors-2024-01-02")
        self.assertEqual(first["anchor_factor_provenance"]["revision_id"], "cumulative_factors-2024-01-03")
        self.assertEqual(result.context["derivation"]["decision_cutoff"], CUTOFF)
        self.assertIn("fixture historical availability assumption", result.context["limitations"])
        json.dumps(result.to_json(), allow_nan=False)

    def test_missing_anchor_fails_instead_of_guessing_future_factor(self):
        prices, factors = inputs()
        factors = batch("cumulative_factors", "adj_factor", {EARLY: 1.0}, sessions=(EARLY,))
        with self.assertRaisesRegex(QueryError, "explicit anchor"):
            transformed(prices, factors)

    def test_missing_and_invalid_factors_produce_nulls_with_reasons(self):
        prices, factors = inputs()
        factors.frame.loc[0, "adj_factor"] = None
        factors.field_meta["adj_factor"]["by_key"][0]["missing_reason"] = "not_visible_at_cutoff"
        missing = transformed(prices, factors)
        self.assertTrue(pd.isna(missing.frame.loc[0, "close"]))
        self.assertEqual(missing.field_meta["close"]["by_key"][0]["missing_reason"], "missing_factor")
        self.assertEqual(missing.frame.loc[1, "close"], 5.0)
        factors.frame.loc[0, "adj_factor"] = 1.0
        factors.frame.loc[1, "adj_factor"] = None
        missing_anchor = transformed(prices, factors)
        self.assertTrue(missing_anchor.frame["close"].isna().all())
        self.assertEqual([row["missing_reason"] for row in missing_anchor.field_meta["close"]["by_key"]],
                         ["missing_anchor_factor", "missing_anchor_factor"])
        factors.frame.loc[0, "adj_factor"] = 1.0
        factors.frame.loc[1, "adj_factor"] = 0.0
        invalid_anchor = transformed(prices, factors)
        self.assertTrue(invalid_anchor.frame["close"].isna().all())
        self.assertEqual([row["missing_reason"] for row in invalid_anchor.field_meta["close"]["by_key"]],
                         ["invalid_anchor_factor", "invalid_anchor_factor"])
        factors.frame.loc[1, "adj_factor"] = -2.0
        self.assertTrue(transformed(prices, factors).frame["close"].isna().all())

    def test_missing_price_propagates_reader_reason(self):
        prices, factors = inputs()
        prices.frame.loc[0, "close"] = None
        prices.field_meta["close"]["by_key"][0]["missing_reason"] = "not_visible_at_cutoff"
        result = transformed(prices, factors)
        self.assertTrue(pd.isna(result.frame.loc[0, "close"]))
        self.assertEqual(result.field_meta["close"]["by_key"][0]["missing_reason"], "not_visible_at_cutoff")

    def test_rejects_different_snapshot_policy_and_cutoff(self):
        prices, factors = inputs()
        factors.context["snapshot_id"] = "s2"
        with self.assertRaisesRegex(QueryError, "Snapshot IDs"):
            transformed(prices, factors)
        factors.context["snapshot_id"] = "s1"
        factors.context["query"]["pit_policy"] = "market_pit_safe_v1"
        with self.assertRaisesRegex(QueryError, "PIT policies"):
            transformed(prices, factors)
        factors.context["query"]["pit_policy"] = "operational_pit_v1"
        factors.context["query"]["cutoff_by_session"][LATE] = "2024-01-03T11:00:00+00:00"
        with self.assertRaisesRegex(QueryError, "single decision cutoff"):
            transformed(prices, factors)
        factors.context["query"]["cutoff_by_session"][LATE] = "2024-01-03T09:00:00+00:00"
        factors.context["query"]["cutoff_by_session"][EARLY] = "2024-01-03T09:00:00+00:00"
        with self.assertRaisesRegex(QueryError, "same decision cutoff"):
            transformed(prices, factors)

    def test_rejects_future_anchor_and_market_replay(self):
        prices, factors = inputs()
        future = "2024-01-04"
        factors = batch("cumulative_factors", "adj_factor",
                        {EARLY: 1.0, LATE: 2.0, future: 2.0}, sessions=(EARLY, LATE, future))
        with self.assertRaisesRegex(QueryError, "future adjustment anchor"):
            adjust_prices(prices, factors, fields=("close",), anchor_session=future)
        with self.assertRaisesRegex(QueryError, "latest price session"):
            adjust_prices(prices, factors, fields=("close",), anchor_session=EARLY,
                          decision_session=EARLY)
        prices, factors = inputs()
        prices.context["query"]["purpose"] = "market_replay"
        factors.context["query"]["purpose"] = "market_replay"
        with self.assertRaisesRegex(QueryError, "market replay"):
            transformed(prices, factors)

    def test_inputs_are_unchanged_and_output_is_detached(self):
        prices, factors = inputs()
        price_before, factor_before = deepcopy(prices.to_json()), deepcopy(factors.to_json())
        result = transformed(prices, factors)
        self.assertEqual(prices.to_json(), price_before)
        self.assertEqual(factors.to_json(), factor_before)
        result.frame.loc[0, "close"] = 900
        result.field_meta["close"]["by_key"][0]["price_provenance"]["revision_id"] = "altered"
        result.context["query"]["fields"].append("other")
        self.assertEqual(prices.to_json(), price_before)
        self.assertEqual(factors.to_json(), factor_before)

    def test_reader_invisible_later_factor_is_not_backfilled_into_old_decision(self):
        class Store:
            def __init__(self):
                self.manifest = {"snapshot_id": "s1", "domains": {}}
                self.rows = {}
                for domain, field, values in (
                    ("market_daily", "close", (10.0, 5.0)),
                    ("cumulative_factors", "adj_factor", (1.0, 2.0)),
                ):
                    self.manifest["domains"][domain] = {
                        "contract": {"contract_id": domain + "_v1",
                                     "logical_key": ["security_id", "session"],
                                     "fields": {field: {"dtype": "float64"}}},
                        "source_profile": {"id": "fixture"},
                        "partitions": [{"partition": domain}], "coverage": {},
                    }
                    self.rows[domain] = [
                        {"security_id": "A", "session": session, field: value,
                         "revision_id": session, "revision_sequence": 1,
                         "first_observed_at": (
                             "2024-01-03T11:00:00+00:00"
                             if domain == "cumulative_factors" and session == LATE
                             else "2024-01-03T09:00:00+00:00"),
                         "raw_batch_id": domain + session,
                         "source_available_at": None, "evidence_ref": None}
                        for session, value in zip((EARLY, LATE), values)
                    ]

            def load_snapshot(self, snapshot_id):
                return deepcopy(self.manifest)

            def read_partition(self, part, *, columns, symbols, sessions):
                return pa.Table.from_pylist([
                    {column: row.get(column) for column in columns}
                    for row in self.rows[part["partition"]]
                    if row["security_id"] in symbols and row["session"] in sessions
                ])

        reader = SnapshotQueryReader(Store(), "s1")

        def read(domain, field, cutoff):
            query = QuerySpec(domain, (field,), ("A",), (EARLY, LATE),
                              "operational_pit_v1", {EARLY: cutoff, LATE: cutoff})
            return reader.read(query)

        early = transformed(read("market_daily", "close", CUTOFF),
                            read("cumulative_factors", "adj_factor", CUTOFF))
        self.assertTrue(early.frame["close"].isna().all())
        self.assertEqual(early.field_meta["close"]["by_key"][0]["missing_reason"],
                         "missing_anchor_factor")
        later = "2024-01-03T12:00:00+00:00"
        visible = transformed(read("market_daily", "close", later),
                              read("cumulative_factors", "adj_factor", later))
        self.assertEqual(visible.frame["close"].tolist(), [5.0, 5.0])


if __name__ == "__main__":
    unittest.main()
