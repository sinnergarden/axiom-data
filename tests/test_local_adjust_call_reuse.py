"""Small correctness and call-count checks for invocation-local adjustment reuse."""

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import unittest
from unittest.mock import patch

import pandas as pd

from axiom_data import derived
from axiom_data.protocols import DataBatch, QueryError, QuerySpec
from axiom_data.reader import SnapshotQueryReader
from test_local_derived import batch
from test_local_reader import MemoryStore, daily_manifest, daily_row


SESSIONS = ("2024-01-02", "2024-01-03", "2024-01-04")
CUTOFF = "2024-01-04T10:00:00+00:00"
AVAILABLE = "2024-01-03T09:00:00+00:00"


def multi_inputs():
    """Unordered native rows, two fields and distinct anchors per security."""
    prices = batch("market_daily", "close", {}, sessions=SESSIONS,
                   cutoffs={s: CUTOFF for s in SESSIONS})
    factors = batch("cumulative_factors", "adj_factor", {}, sessions=SESSIONS,
                    cutoffs={s: CUTOFF for s in SESSIONS})
    for value, fields in ((prices, ("close", "open")), (factors, ("adj_factor",))):
        records = []
        metadata = {field: {"dtype": "float64", "unit": "CNY" if value is prices else None,
                            "by_key": []} for field in fields}
        for index, session in enumerate(SESSIONS):
            for symbol in ("A", "B"):
                record = {"security_id": symbol, "session": session}
                for field in fields:
                    record[field] = ((100 if field == "close" else 200)+index+(10 if symbol == "B" else 0)
                                     if value is prices else
                                     (7.0 if symbol == "A" else 11.0) if index == 2 else float(index+1))
                    metadata[field]["by_key"].append({"security_id": symbol, "session": session,
                        "revision_id": f"{field}/{symbol}/{session}", "revision_sequence": 1,
                        "raw_batch_id": "synthetic", "usable_from": AVAILABLE,
                        "first_observed_at": AVAILABLE, "availability_basis": "first_observed_at",
                        "missing_reason": None, "evidence_ref": None,
                        "unknown_attributes": {"keep": [None, -0.0, "original"]}})
                records.append(record)
        # DataBatch is frozen; replace its owned frame/metadata through construction.
        result = DataBatch(pd.DataFrame.from_records(records[::-1]), metadata, deepcopy(value.context))
        result.context["query"].update(fields=list(fields), symbols=["B", "A"])
        if value is prices:
            new_prices = result
        else:
            new_factors = result
    return new_prices, new_factors


def adjusted(prices, factors, **options):
    return derived.adjust_prices(prices, factors, fields=("close", "open"),
                                 anchor_session=SESSIONS[-1], **options)


class AdjustmentCallReuseTests(unittest.TestCase):
    def test_anchor_numbers_are_parsed_once_per_security(self):
        prices, factors = multi_inputs()
        with patch.object(derived, "_number", wraps=derived._number) as numbers:
            result = adjusted(prices, factors)
        # Each anchor is parsed once as an anchor and once as its daily factor.
        seen = Counter(call.args[0] for call in numbers.call_args_list)
        self.assertEqual((seen[7.0], seen[11.0], numbers.call_count), (2, 2, 20))
        keys = [(s, d) for d in SESSIONS for s in ("B", "A")]
        self.assertEqual(list(zip(result.frame.security_id, result.frame.session)), keys)
        values = result.frame.set_index(["security_id", "session"])
        self.assertEqual(values.loc[("A", SESSIONS[0]), "close"], 100.0/7.0)
        self.assertEqual(values.loc[("B", SESSIONS[0]), "close"], 110.0/11.0)
        self.assertEqual(values.loc[("A", SESSIONS[-1]), "close"], 102.0)

    def test_equal_timestamp_strings_are_parsed_once_in_one_call(self):
        prices, factors = multi_inputs()
        with patch.object(derived, "_instant", wraps=derived._instant) as instants:
            result = adjusted(prices, factors)
        self.assertEqual(Counter(c.args[0] for c in instants.call_args_list),
                         Counter({CUTOFF: 1, AVAILABLE: 1}))
        self.assertEqual(result.context["derivation"]["decision_cutoff"], CUTOFF)

    def test_string_memo_is_bounded_and_does_not_evict_then_reparse_earlier_strings(self):
        days = tuple((date(2024, 1, 1)+timedelta(days=i)).isoformat() for i in range(130))
        cutoff = "2024-06-01T10:00:00+00:00"
        prices = batch("market_daily", "close", dict.fromkeys(days, 10.0), sessions=days,
                       cutoffs=dict.fromkeys(days, cutoff))
        factors = batch("cumulative_factors", "adj_factor", dict.fromkeys(days, 2.0), sessions=days,
                        cutoffs=dict.fromkeys(days, cutoff))
        stamps = [(datetime(2024, 1, 1, tzinfo=timezone.utc)+timedelta(seconds=i)).isoformat()
                  for i in range(130)]
        for value in (prices, factors):
            for item, stamp in zip(next(iter(value.field_meta.values()))["by_key"], stamps):
                item["usable_from"] = stamp
        with patch.object(derived, "_instant", wraps=derived._instant) as instants:
            result = derived.adjust_prices(prices, factors, fields=("close",), anchor_session=days[-1])
        seen = Counter(c.args[0] for c in instants.call_args_list)
        self.assertEqual(seen[cutoff], 1)
        self.assertTrue(all(seen[s] == 1 for s in stamps[:127]))
        self.assertTrue(all(seen[s] == 2 for s in stamps[127:]))
        self.assertEqual(instants.call_count, 134)
        self.assertEqual(result.frame.close.tolist(), [10.0]*130)

    def test_nonstring_instants_keep_the_original_parser_path(self):
        prices, factors = multi_inputs()
        cutoff = datetime.fromisoformat(CUTOFF)
        available = datetime.fromisoformat(AVAILABLE)
        for value in (prices, factors):
            value.context["query"]["cutoff_by_session"] = dict.fromkeys(SESSIONS, cutoff)
            for definition in value.field_meta.values():
                for item in definition["by_key"]:
                    item["usable_from"] = available
        with patch.object(derived, "_instant", wraps=derived._instant) as instants:
            result = adjusted(prices, factors)
        self.assertEqual(instants.call_count, 24)
        self.assertEqual(result.context["derivation"]["decision_cutoff"], CUTOFF)

    def test_provenance_comparisons_still_reject_late_or_naive_values(self):
        for domain, stamp, message in (("prices", "2024-01-04T10:00:01+00:00", "prices provenance"),
                                       ("factors", "2024-01-04T10:00:01+00:00", "factors provenance"),
                                       ("prices", "2024-01-03T09:00:00", "prices usable_from")):
            with self.subTest(domain=domain, stamp=stamp):
                prices, factors = multi_inputs()
                target = prices if domain == "prices" else factors
                next(iter(target.field_meta.values()))["by_key"][-1]["usable_from"] = stamp
                with self.assertRaisesRegex(QueryError, message):
                    adjusted(prices, factors)

    def test_repeated_calls_do_not_reuse_admission_or_mutated_anchor_values(self):
        prices, factors = multi_inputs()
        first = adjusted(prices, factors)
        factors.frame.loc[(factors.frame.security_id == "A") &
                          (factors.frame.session == SESSIONS[-1]), "adj_factor"] = 14.0
        second = adjusted(prices, factors)
        self.assertEqual(second.frame.set_index(["security_id", "session"]).loc[
            ("A", SESSIONS[0]), "close"], first.frame.set_index(["security_id", "session"]).loc[
                ("A", SESSIONS[0]), "close"]/2)
        early = "2024-01-03T08:00:00+00:00"
        for value in (prices, factors):
            value.context["query"]["cutoff_by_session"] = dict.fromkeys(SESSIONS, early)
        with self.assertRaisesRegex(QueryError, "factors provenance"):
            adjusted(prices, factors)

    def test_missing_unknown_and_invalid_anchors_keep_per_cell_reason_precedence(self):
        prices, factors = multi_inputs()
        for symbol, number in (("A", None), ("B", 0.0)):
            factors.frame.loc[(factors.frame.security_id == symbol) &
                              (factors.frame.session == SESSIONS[-1]), "adj_factor"] = number
        for item in factors.field_meta["adj_factor"]["by_key"]:
            if item["security_id"] == "A" and item["session"] == SESSIONS[-1]:
                item.update(usable_from=None, missing_reason="unknown")
        prices.frame.loc[(prices.frame.security_id == "B") &
                         (prices.frame.session == SESSIONS[0]), "close"] = None
        for item in prices.field_meta["close"]["by_key"]:
            if item["security_id"] == "B" and item["session"] == SESSIONS[0]:
                item["missing_reason"] = "source_unknown"
        result = adjusted(prices, factors)
        self.assertTrue(result.frame[["close", "open"]].isna().all().all())
        for field in ("close", "open"):
            for item in result.field_meta[field]["by_key"]:
                reason = ("missing_anchor_factor" if item["security_id"] == "A" else
                          "source_unknown" if field == "close" and item["session"] == SESSIONS[0]
                          else "invalid_anchor_factor")
                self.assertEqual(item["missing_reason"], reason)

    def test_input_output_and_sibling_lineage_are_isolated(self):
        prices, factors = multi_inputs()
        prices.context["diagnostic_symbols"] = prices.context["query"]["symbols"]
        before = prices.to_json(), factors.to_json()
        result = adjusted(prices, factors)
        result.field_meta["close"]["by_key"][0]["anchor_factor_provenance"]["unknown_attributes"]["keep"].append("bad")
        self.assertEqual(result.field_meta["open"]["by_key"][0]["anchor_factor_provenance"]["unknown_attributes"]["keep"],
                         [None, -0.0, "original"])
        self.assertEqual(result.field_meta["close"]["by_key"][2]["anchor_factor_provenance"]["unknown_attributes"]["keep"],
                         [None, -0.0, "original"])
        result.context["query"]["symbols"].append("C")
        self.assertEqual(result.context["diagnostic_symbols"], ["B", "A"])
        self.assertEqual((prices.to_json(), factors.to_json()), before)
        self.assertEqual(list(result.context), list(prices.context)+["derivation"])

    def test_reader_cutoffs_choose_old_and_new_price_and_anchor_revisions(self):
        manifest = daily_manifest()
        market = manifest["domains"]["market_daily"]
        market["partitions"] = [{"partition": "prices"}]
        factor = deepcopy(market)
        factor["partitions"] = [{"partition": "factors"}]
        factor["contract"]["fields"] = {"adj_factor": {"dtype": "float64"}}
        manifest["domains"]["cumulative_factors"] = factor
        early, late = SESSIONS[:2]
        known = datetime(2024, 1, 3, 9, tzinfo=timezone.utc)
        revised = datetime(2024, 1, 3, 11, tzinfo=timezone.utc)
        old_price = daily_row(session=early, observed=known, close=10.0, revision="price-old")
        new_price = daily_row(session=early, observed=revised, close=30.0, revision="price-new", sequence=2)
        anchor_price = daily_row(session=late, observed=known, close=5.0, revision="anchor-price")
        factor_rows = []
        for session, value, revision, sequence, observed in (
            (early, 1.0, "factor", 1, known), (late, 2.0, "anchor-old", 1, known),
            (late, 4.0, "anchor-new", 2, revised)):
            row = daily_row(session=session, observed=observed, revision=revision, sequence=sequence)
            row["adj_factor"] = value
            factor_rows.append(row)
        reader = SnapshotQueryReader(MemoryStore(manifest, {"prices": [old_price, new_price, anchor_price],
                                                           "factors": factor_rows}), "s1", cache_bytes=0)
        for hour, values, revisions in ((10, [5.0, 5.0], ("price-old", "anchor-old")),
                                        (12, [7.5, 5.0], ("price-new", "anchor-new")),
                                        (10, [5.0, 5.0], ("price-old", "anchor-old"))):
            cutoff = datetime(2024, 1, 3, hour, tzinfo=timezone.utc)
            price = reader.read(QuerySpec("market_daily", ("close",), ("A",), (early, late),
                                         "operational_pit_v1", {early: cutoff, late: cutoff}))
            factors = reader.read(QuerySpec("cumulative_factors", ("adj_factor",), ("A",), (early, late),
                                           "operational_pit_v1", {early: cutoff, late: cutoff}))
            result = derived.adjust_prices(price, factors, fields=("close",), anchor_session=late)
            self.assertEqual(result.frame.close.tolist(), values)
            meta = result.field_meta["close"]["by_key"][0]
            self.assertEqual((meta["price_provenance"]["revision_id"], meta["anchor_factor_provenance"]["revision_id"]), revisions)


if __name__ == "__main__":
    unittest.main()
